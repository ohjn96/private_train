/** @type {import('tailwindcss').Config} */
// 화면 CSS(webui/static/css/app.css)는 이 설정으로 미리 만들어 저장소에 넣는다.
// 템플릿의 클래스를 바꾸면 다시 만든다: scripts/build_css.sh (Tailwind 3.4.17)
module.exports = {
  content: [
    './webui/templates/**/*.html',
    // JS 가 붙이는 클래스도 템플릿 안의 <script> 에 통째 문자열로 있어서 위에서 같이 잡힌다
    './webui/static/js/**/*.js',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans KR"', 'system-ui', 'sans-serif'],
        // Plex Mono 에는 한글이 없어서, 한글은 같은 계열의 Plex Sans KR 로 그린다
        mono: ['"IBM Plex Mono"', '"IBM Plex Sans KR"', 'ui-monospace', 'monospace'],
      },
      colors: {
        // 강조색: 앱 아이콘과 같은 파랑 (예전 이름 그대로 두어 기존 화면이 새 색을 따르게)
        ktx: {
          primary: '#1E4FD8',
          secondary: '#1740B0',
          light: '#BFD0F7',
        },
        // "레일 티켓" 팔레트: 따뜻한 미색 바탕 + 먹색 글자
        rail: {
          ground: '#F6F4F0',
          ink: '#1A1714',
          inkSoft: '#4A443E',     // 로그 글자
          muted: '#6B645C',
          muted2: '#8A8279',      // 꺼진 버튼 글자, 어두운 칸의 보조 글자
          muted3: '#9A938A',      // 아직 못 가는 단계
          line: '#E6E1DA',
          lineSoft: '#EFEBE5',    // 목록 안 구분선
          lineMid: '#CFC9C1',     // 점선 동그라미
          lineStrong: '#D6D0C8',  // 보조 버튼 테두리, 선택 표시
          subtle: '#FAF8F5',      // 표 머리
          sunken: '#EDE9E3',      // 빈 화면 아이콘 바탕
          disabled: '#ECE8E2',    // 꺼진 버튼 바탕
          accentSoft: '#E8EEFC',
          accentFaint: '#F3F6FE', // 고른 열차 카드 바탕
          // 어두운(먹색) 칸 위의 글자
          onDark: '#E8E2D9',
          onDarkMuted: '#C9C2B8',
          // 위험·실패 알림은 강조색이 파랑이어도 빨강으로 둔다 (자동결제 실패, 오류)
          danger: '#C8102E',
          dangerDark: '#9E0C24',
          dangerSoft: '#FBE9EB',
          dangerLine: '#F1C4BF',
          ok: '#0B6B3A',
          okSoft: '#E3F4EA',
          okInk: '#245B3D',       // 초록 안내 본문
          okDot: '#16A34A',       // 켜짐 점
          okGlow: '#4ADE80',      // 먹색 위의 실행 중 점
          // 주의(결제 남음·배터리 최적화 등): 호박색
          warn: '#B45309',
          warnDark: '#8A4B00',
          warnSoft: '#FDF1DD',
          warnInk: '#7A4200',     // 주의 제목
          warnBody: '#6B4A1E',    // 주의 본문
          warnGlow: '#FDE68A',    // 먹색 위의 주의 글자
          off: '#5A544D',
          offSoft: '#EFECE7',
        },
      },
    },
  },
  plugins: [],
};
