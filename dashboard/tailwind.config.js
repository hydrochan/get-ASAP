// Tailwind 빌드 설정 — build_css.py 가 .tools/tailwindcss-windows-x64.exe (v3.4.17) 로 실행한다.
// 예전 index.html 인라인 `tailwind.config` (Play CDN) 와 같은 theme.extend 를 그대로 옮긴 것이다.
/** @type {import('tailwindcss').Config} */
module.exports = {
  // relative: true -> 실행 위치(cwd)와 무관하게 이 설정 파일 기준 경로로 스캔한다.
  content: { relative: true, files: ['./index.html'] },
  // 클래스명을 문자열 조합으로 만드는 코드(`bg-${x}`)는 없다 — 전부 완성된 리터럴이라 스캐너가 잡는다.
  // 앞으로 조합식 클래스를 쓰면 여기에 safelist 로 추가할 것.
  safelist: [],
  theme: {
    extend: {
      fontFamily: { sans: ['Manrope', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'system-ui', 'sans-serif'] },
      colors: {
        navy: { 50: '#E8EEF8', 900: '#0F2A55', 800: '#163B73', 700: '#1F4A8C', 600: '#2A5BA8' },
        slate: { 50: '#F8F9FA', 100: '#F3F4F5', 200: '#E5E7EB', 300: '#D1D5DB', 400: '#9CA3AF', 500: '#6B7280' },
      },
    },
  },
  plugins: [],
};
