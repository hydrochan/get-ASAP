# Cloudflare Access 로그인 (`/manage`)

관리자 진입 `GET /manage` 를 nginx Basic 인증 대신 Cloudflare Access(Zero Trust, Google 로그인)로 보호한다.
원본 VM 은 IP 로도 직접 닿으므로 **헤더 값은 믿지 않는다** — 앱이 `Cf-Access-Jwt-Assertion`(RS256 JWT)의
서명·`aud`·`iss`·`exp`·`iat`·`email` 을 Cloudflare 공개키(`{ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`)로 직접 검증한다.
`Cf-Access-Authenticated-User-Email` 헤더는 인증에 쓰지 않는다. 구현: `dashboard/access_auth.py`, 진입점: `dashboard/server.py` 의 `/manage` 핸들러.

## 환경변수 (`.env`, 셋 다 있어야 켜진다)

| 변수 | 내용 |
|---|---|
| `ACCESS_TEAM_DOMAIN` | `https://<team>.cloudflareaccess.com` (끝 슬래시 없이, JWT `iss` 와 정확히 일치. `https://` 아니면 꺼짐) |
| `ACCESS_AUD` | Access 애플리케이션의 AUD 태그 (Zero Trust > Access > Applications > 해당 앱 > Overview) |
| `ACCESS_EMAIL_USERS` | 로그인 이메일 → 대시보드 사용자명 JSON 한 줄. 이메일 비교는 대소문자 무시. 사용자명은 `DASHBOARD_ADMINS` 에 있어야 한다 |

하나라도 비어 있으면 기능이 꺼지고 기존 `X-Basic-User`(nginx Basic) 경로만 동작한다. 서버 시작 로그에 켜짐/꺼짐이 한 줄 남는다.

## `/manage` 동작

- `Cf-Access-Jwt-Assertion` 헤더가 있고 기능이 켜져 있으면: JWT 검증 → 이메일을 사용자명으로 매핑 → 그 사용자가 `DASHBOARD_ADMINS` 면 세션 쿠키를 심고 `/` 로 302. 검증 실패·만료·aud/iss 불일치·매핑 없음·관리자 아님은 모두 본문 없는 403 이고, 사유(토큰 제외)만 서버 로그에 WARNING 으로 남는다. 헤더가 있는데 검증이 실패하면 Basic 으로 되돌아가지 **않는다**.
- 헤더가 없으면(전환 기간): 기존 `X-Basic-User` 경로 그대로.
- 성공 로그: `Access 로그인 성공: email=... user=...` (INFO).
- 공개키는 메모리에 캐시(1시간)하고, 모르는 kid(키 교체)일 때만 다시 가져온다. 갱신 시도는 10초에 한 번으로 제한되어, 위조 kid 가 몰려도 Cloudflare 호출은 늘지 않는다. 키 교체 직후 첫 로그인이 10초 안에 거부되면 잠시 뒤 다시 열면 된다.

## 배포 순서 (VM)

1. Cloudflare Zero Trust 에서 `/manage` 경로(호스트명 + 경로 `manage`)에 Self-hosted 애플리케이션을 만들고 정책에 허용 이메일을 지정한다. AUD 태그와 팀 도메인을 확인한다.
2. VM 에서:
   ```bash
   cd ~/get-ASAP && git pull
   .venv/bin/pip install -r requirements.txt
   # .env 에 ACCESS_TEAM_DOMAIN / ACCESS_AUD / ACCESS_EMAIL_USERS 추가
   sudo systemctl restart get-asap-dashboard
   journalctl -u get-asap-dashboard -n 20 --no-pager   # "Cloudflare Access 로그인(/manage): 켜짐" 확인
   ```
3. 확인: 브라우저로 `https://<도메인>/manage` → Google 로그인 → `/` 로 이동해 관리자 모드(Review 탭 등)로 열리는지. 로그에 `Access 로그인 성공` 이 찍혀야 한다. 이 단계에서는 nginx Basic 이 아직 살아 있어 Basic 창도 한 번 뜰 수 있다(전환 기간).
   - 쿠키 `Secure` 플래그는 `X-Forwarded-Proto` 가 `https` 일 때만 붙는다. nginx 의 해당 헤더가 Cloudflare→원본 구간 스킴(`$scheme`)이면 `http` 로 올 수 있으니 DevTools 에서 쿠키를 확인한다.

## nginx 변경 (Access 동작을 확인한 **뒤에** 적용)

`location = /manage` 블록에서 `auth_basic`, `auth_basic_user_file` 줄을 지우고, `X-Basic-User` 를 비운다. 나머지(`proxy_pass`, `Host`, `X-Real-IP`, `X-Forwarded-*`)는 그대로 둔다.

```nginx
location = /manage {
    proxy_pass http://127.0.0.1:8501;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Basic-User "";          # Basic 경로 폐쇄: 클라이언트가 보낸 값도 지운다
}
```

`sudo nginx -t && sudo systemctl reload nginx`. 이후 IP 로 직접 `/manage` 를 열면 JWT 도 Basic 도 없으므로 403 이다. `Cf-Access-Jwt-Assertion` 을 위조해도 서명 검증에서 막힌다.

## 롤백

- nginx: 위 블록을 Basic 이 있던 원래 블록(`auth_basic`, `auth_basic_user_file`, `proxy_set_header X-Basic-User $remote_user;`)으로 되돌리고 `nginx -t && reload`. 앱 쪽은 헤더가 없을 때 Basic 경로를 그대로 쓰므로 코드 변경 없이 복구된다.
- 앱 쪽 Access 자체를 끄려면 `.env` 의 `ACCESS_*` 중 하나를 비우고 `sudo systemctl restart get-asap-dashboard`.
