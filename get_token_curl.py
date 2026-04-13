"""SSL 문제 우회: 브라우저에서 auth code 받고 curl로 토큰 교환

Windows 로컬 환경에서 Python requests의 SSL 에러 발생 시 사용.
브라우저 OAuth 인증 → curl로 토큰 교환 → token.json 저장.
"""
import json
import subprocess
import secrets
import hashlib
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

# credentials.json 로드
with open("credentials.json") as f:
    creds = json.load(f)["installed"]

CLIENT_ID = creds["client_id"]
CLIENT_SECRET = creds["client_secret"]
TOKEN_URI = creds["token_uri"]
SCOPE = "https://www.googleapis.com/auth/gmail.modify"

# PKCE 코드 생성
code_verifier = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b"=").decode()

PORT = 58923
REDIRECT_URI = f"http://localhost:{PORT}/"

auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        qs = parse_qs(urlparse(self.path).query)
        auth_code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>OK! Close this tab.</h1>")
    def log_message(self, *args):
        pass

# 1. 브라우저에서 인증
params = urlencode({
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPE,
    "access_type": "offline",
    "prompt": "consent",
    "code_challenge": code_challenge,
    "code_challenge_method": "S256",
})
auth_url = f"https://accounts.google.com/o/oauth2/auth?{params}"

import webbrowser
print("브라우저에서 인증 중...")
webbrowser.open(auth_url)

server = HTTPServer(("localhost", PORT), Handler)
server.handle_request()

if not auth_code:
    print("ERROR: auth code를 받지 못했습니다.")
    exit(1)

print("Auth code 수신 완료!")

# 2. curl로 토큰 교환 (Python requests SSL 문제 우회)
curl_cmd = [
    "curl", "-s", "-X", "POST", TOKEN_URI,
    "-d", f"code={auth_code}",
    "-d", f"client_id={CLIENT_ID}",
    "-d", f"client_secret={CLIENT_SECRET}",
    "-d", f"redirect_uri={REDIRECT_URI}",
    "-d", "grant_type=authorization_code",
    "-d", f"code_verifier={code_verifier}",
]

result = subprocess.run(curl_cmd, capture_output=True, text=True)
token_data = json.loads(result.stdout)

if "error" in token_data:
    print(f"ERROR: {token_data}")
    exit(1)

# 3. token.json 형식으로 저장
token_json = {
    "token": token_data["access_token"],
    "refresh_token": token_data.get("refresh_token"),
    "token_uri": TOKEN_URI,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "scopes": [SCOPE],
}

with open("token.json", "w") as f:
    json.dump(token_json, f, indent=2)

print("token.json 저장 완료!")
