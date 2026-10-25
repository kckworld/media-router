# 클로드 분야별 필수 도구 50개 — 설치·사용 가이드

> 원문: [노션 페이지](https://grand-count-2aa.notion.site/50-40250157ef13805aae57f01976f390ba) (2026-10-26 가져옴). Media Router 설정 > Claude 스킬에서 바로 설치할 수 있는 건 거기서 설치하세요.

> 카드뉴스에서 소개한 50개 도구를 분야별로 정리했습니다.
50개 다 깔 필요 없어요. 내 분야 칸의 도구부터 하나씩 하세요.

---

### 0. 시작 전에 (1분)
이 가이드는 대부분 내 컴퓨터에서 실행하는 클로드코드 기준입니다.
클로드 웹(앱)에서 되는 것은 항목마다 따로 표시했습니다.
클로드코드가 아직 없다면 아래 페이지부터 따라 하고 돌아오세요. (클로드코드 터미널 설치법)
https://grand-count-2aa.notion.site/39c50157ef1380b29de7f10bb3f6d78f
명령어를 넣는 곳은 딱 두 군데입니다.
- ⚫ 일반 터미널: 클로드코드를 실행하기 전의 그냥 터미널 (새 터미널 창을 하나 더 열어도 됩니다)
- 🟠 클로드코드 입력창: 터미널에서 `claude`를 실행하면 나오는 대화창
각 항목마다 어디에 넣는지 색깔로 표시해 뒀습니다. 명령어는 한 줄씩 붙여넣고 엔터를 누르세요.
설치 중에 어디에 설치할지 물어보면 `User`를 고르세요. 이 컴퓨터의 모든 프로젝트에서 쓸 수 있습니다.
설치 후 안 보이면? 설치 완료 안내를 먼저 따르고, 그래도 안 보이면 클로드코드를 완전히 껐다가 다시 켜세요. 스킬을 폴더로 넣은 경우는 클로드코드에서 `/reload-plugins`를 한 번 쳐주세요.
비용
도구 자체는 대부분 무료입니다. 클로드 구독·API 요금은 별도이고, 유료 계정·API 키가 필요한 것은 항목마다 표시했습니다.

---

### 1. 프롬프트
#### 1-1. prompts.chat — 잘 쓴 프롬프트 골라 쓰기
뭘 해주나요?
"뭐라고 물어봐야 하지?" 고민될 때, 전 세계 사람들이 공개해 둔 프롬프트 모음에서 내 상황에 맞는 걸 찾아줍니다. 내가 쓴 프롬프트를 더 좋게 다듬어 달라고 할 수도 있습니다.
설치
클로드코드 안에서 아래 두 줄을 한 줄씩 차례로 입력합니다.
```
/plugin marketplace add f/prompts.chat
```
```
/plugin install prompts.chat@prompts.chat
```
- 설치 범위를 고르라고 나오면 User(사용자) 를 고르세요. 이게 전역 설치입니다.
- 설치 완료 안내를 따르고, 명령어가 안 보이면 클로드코드를 껐다 켜세요.
바로 써보기
```
/prompts.chat:prompts marketing
```
`marketing` 자리에 찾고 싶은 주제를 넣으면 됩니다. 검색 결과가 부족하면 marketing, email처럼 영어 키워드로도 검색해 보세요.
마음에 드는 걸 찾았으면 이렇게 이어서 말하세요.
```
방금 찾은 프롬프트를 한국어로 바꾸고, 우리 가게(동네 카페) 상황에 맞게 고쳐줘.
```
활용 예시
주의
- 찾기와 다듬기는 가입 없이 됩니다. 내 프롬프트를 prompts.chat에 저장하려면 그 사이트에서 키를 따로 발급받아야 하는데, 처음엔 필요 없습니다.
- 누구나 올릴 수 있는 공개 모음이라 품질이 고르지 않습니다. 그대로 쓰지 말고 내 상황에 맞게 고쳐 쓰세요.

---

#### 1-2. grill-me — 시키기 전에 클로드가 먼저 질문
계획·결정·아이디어를 던지면 AI가 바로 만들기 시작하는 대신 먼저 나를 인터뷰하는 무료 스킬입니다.
질문마다 추천 답이 같이 오니까 참고해서 고르거나 고치면 되고, 애매한 부분이 정리된 뒤에 만들기 시작해서 엇나간 결과와 재작업을 줄이는 데 도움이 됩니다.
- 원본: https://github.com/mattpocock/skills (스킬 모음 중 하나, MIT 라이선스 무료)
- 스킬 페이지: https://www.skills.sh/mattpocock/skills/grill-me
어디에 쓰나요

---

아래 페이지에 해당스킬관련해서 상세하게 정리해두었으니 참고하시면 됩니다.
[https://grand-count-2aa.notion.site/grill-me-3e950157ef1380d4a675ff1042a42f65](https://grand-count-2aa.notion.site/grill-me-3e950157ef1380d4a675ff1042a42f65)
#### 1-3. prompt-master — 내 프롬프트 다듬어주기
뭘 해주나요?
"이거 만들어줘"처럼 대충 쓴 요청을, 쓰려는 AI 도구에 맞는 정확한 프롬프트로 바꿔줍니다. 클로드·ChatGPT·제미나이뿐 아니라 미드저니·클로드코드·커서 등 20개 넘는 도구별 프로필이 들어 있어서, "클로드코드용 프롬프트 써줘"라고 하면 그 도구에 맞는 형식으로 나옵니다. 긴 프롬프트가 아니라 날카로운 프롬프트를 만드는 게 목표인 스킬입니다.
설치 — 클로드 웹(앱) (공식 추천 방법, 설치 가장 쉬움)
1. github.com/nidhinjs/prompt-master 접속 → 초록색 Code 버튼 → Download ZIP
1. claude.ai → 왼쪽 메뉴 사용자 지정(Customize) → 스킬(Skills)
1. + → 스킬 만들기 → 스킬 업로드 → 받은 ZIP 파일 올리기
설치 — 클로드코드 ⚫ 일반 터미널
윈도우 PowerShell:
```
git clone https://github.com/nidhinjs/prompt-master.git $env:USERPROFILE\.claude\skills\prompt-master
```
맥:
```
git clone https://github.com/nidhinjs/prompt-master.git ~/.claude/skills/prompt-master
```
끝나면 클로드코드에서 `/reload-plugins`
바로 써보기
```
클로드코드로 카페 예약 페이지를 만들 건데, 그 프롬프트를 써줘. 필요한 건 나한테 먼저 물어봐.
```
```
이 프롬프트를 고쳐줘: 블로그 글 써줘 AI 관련으로
```
```
/prompt-master
```
주의
- 스킬 본문이 커서(약 8천 토큰) 켜질 때 사용량을 꽤 씁니다. 프롬프트가 중요한 작업에만 쓰고, 간단한 요청에는 굳이 안 써도 됩니다.
- 자동으로 안 켜지면 "prompt-master 스킬로"라고 이름을 붙여 부르세요.
참고 깃허브: github.com/nidhinjs/prompt-master · 무료

---

#### 1-4. i-have-adhd — 답을 짧게 쪼개서 받기
뭘 해주나요?
클로드가 "좋은 질문이에요!" 하면서 세 문단 쓰고 정작 답은 밑에 두는 습관을 없앱니다. 결론부터, 단계는 번호로, 인사말 없이 답합니다. 이름은 ADHD인데 진단 필요 없습니다. 답변 길어서 안 읽는 분이면 다 해당됩니다.
설치 (제일 쉬운 방법) 🟠 클로드코드에 아래 문장을 그대로 붙여넣으면 클로드가 알아서 설치합니다.
```
Install the i-have-adhd skill/plugin from https://github.com/ayghri/i-have-adhd, refer to the repo's AGENTS.md for instructions.
```
설치 (직접 하는 방법) 🟠 한 줄씩
```
/plugin marketplace add ayghri/i-have-adhd
```
```
/plugin install i-have-adhd@i-have-adhd
```
```
/reload-plugins
```



바로 써보기
설치 전에 아래 질문을 한 번 던져서 답변 길이를 봐두세요. 설치 후 같은 질문을 다시 던지면 차이가 보입니다.
```
클로드코드 스킬이 뭔지 설명해줘
```
스킬을 직접 부르고 싶으면:
```
/i-have-adhd 우리 팀 회의록 요약하는 방법 알려줘
```
이 스킬이 지키는 규칙 중 핵심
- 다음 할 일부터 먼저 말한다
- 여러 단계는 번호를 매긴다
- 곁가지 얘기를 하지 않는다
- 목록은 5개까지만
- 서론, 요약, 마무리 인사 없음
참고 깃허브: github.com/ayghri/i-have-adhd · 한국어 README 있음

---

#### 1-5. Karpathy Skills — 엉뚱한 작업 안 하게
뭘 해주나요?
전 테슬라 AI 총괄 안드레이 카르파시가 지적한 AI의 버릇(멋대로 가정하기, 부풀리기, 안 건드릴 걸 건드리기)을 막는 규칙 4개입니다. 코드 작업에서 불필요한 가정과 과한 수정을 줄여줘요.
카르파시 본인이 만든 건 아니고, 그의 지적을 바탕으로 다른 개발자가 만든 도구입니다.
설치 방법 A — 플러그인 🟠 클로드코드 입력창에 한 줄씩
```
/plugin marketplace add forrestchang/andrej-karpathy-skills
```
```
/plugin install andrej-karpathy-skills@karpathy-skills
```
설치 방법 B — 폴더 복사 ⚫ 일반 터미널 (A가 안 될 때)
```
git clone https://github.com/multica-ai/andrej-karpathy-skills.git
cp -r andrej-karpathy-skills/skills/karpathy-guidelines ~/.claude/skills/
```
바로 써보기
```
karpathy-guidelines 스킬을 적용해서 작업해줘.
요구사항이 불명확하면 먼저 질문하고,
요청한 범위만 수정해줘.
```
따로 부를 필요 없이 설치만 해두면 자동 적용됩니다. 코딩 시켜보면 군더더기가 줄어든 게 보여요.
참고 깃허브: github.com/multica-ai/andrej-karpathy-skills

---

### 2. 문서·PPT·엑셀
#### 2-1 ~ 2-4. docx · pptx · xlsx · pdf — 앤트로픽 공식 스킬 4개
뭘 해주나요?
- xlsx — 수식·함수·차트가 들어간 엑셀 파일 제작. "이 데이터로 엑셀 파일 만들어줘"라고 하면 끝.
- pptx — 말로 설명하면 발표 슬라이드 파일을 통째로 제작.
- docx — 목차·표·서식 갖춘 워드 문서 생성, 기존 문서 수정도 가능.
- pdf — PDF 읽기, 내용 추출, 양식 채우기, 새 PDF 생성.
클로드 웹(앱)에서는 설치가 필요 없습니다
"코드 실행 및 파일 생성"만 켜져 있으면, 요청할 때 클로드가 알아서 사용합니다.
1. claude.ai → 왼쪽 하단 프로필 → 설정(Settings)
1. 기능(Capabilities) 메뉴에서 "코드 실행 및 파일 생성" 켜기
클로드코드에서 쓰려면 — 방법 A: 플러그인 🟠
```
/plugin marketplace add anthropics/skills
```
```
/plugin install example-skills@anthropic-agent-skills
```
이 묶음에는 docx·pptx·xlsx·pdf 외에 공식 스킬 여러 개가 같이 들어 있습니다. 같이 깔려도 문제없습니다.
바로 써보기 ("워드로", "PPT로", "엑셀로"라는 말 자체가 트리거입니다)
```
지난 분기 성과 요약 보고서를 워드 파일로 만들어줘. 목차와 표 2개 포함.
```
```
"AI 툴 사내 도입 제안" 발표자료 10장을 pptx로. 문제→해결→기대효과 순서로.
```
```
월별 트래커 엑셀 만들어줘. 조회수·좋아요 넣으면 참여율 자동 계산되게 수식 포함.
```
```
이 PDF 3개를 하나로 합치고, 첫 장 양식에 내 정보 채워줘.
```
참고 깃허브: github.com/anthropics/skills (공식)

---

#### 2-5. book-to-skill — 책·PDF를 스킬로
뭘 해주나요?
PDF, 워드, 텍스트 파일을 넣으면 클로드가 그 내용을 분석해서 스킬로 만들어줍니다. 그다음부터는 명령어 하나로 그 자료 전문가를 불러서 조언을 받습니다. 지어내지 않고 자료 내용대로만 답합니다.
되는 파일 PDF, DOCX(워드), EPUB, MOBI(킨들), HTML, RTF, TXT, 마크다운. 파일 하나가 아니라 폴더 통째로도 됩니다.
안 되는 파일 PPT, 엑셀, 한글(HWP), 스캔한 PDF(글자가 이미지인 것). 한글 파일은 PDF로 저장한 뒤 넣으세요.
설치 ⚫ 일반 터미널
윈도우 PowerShell:
```
git clone https://github.com/virgiliojr94/book-to-skill.git $env:USERPROFILE\.claude\skills\book-to-skill
```
맥 / Git Bash:
```
git clone https://github.com/virgiliojr94/book-to-skill.git ~/.claude/skills/book-to-skill
```
그다음 클로드코드에서 `/reload-plugins`
추출 도구 확인 🟠 클로드코드에서 아래를 치면 뭐가 깔려 있고 뭐가 필요한지 알려줍니다. "optional deps 설치할까?" 물어보면 yes 하세요.
```
book-to-skill 스킬 준비 상태 확인해줘. 필요한 파이썬 패키지 있으면 설치해줘.
```
바로 써보기
1단계. 파일을 클로드코드 작업 폴더에 넣습니다. (예: AI프롬프트실전가이드.pdf)
2단계. 변환 명령어
```
/book-to-skill AI프롬프트실전가이드.pdf
```
중간에 "Text-heavy냐 Technical이냐" 물어보면 일반 문서는 Text-heavy를 고르세요.
3단계. 변환이 끝나면 화면에 생성된 스킬 이름이 뜹니다. `/reload-plugins`
4단계. 전문가에게 질문
```
/ai-prompt-practical-guide 프롬프트의 핵심개념에 대해 설명해줘
```
활용 예시
주의
- 본인이 가진 파일이나 회사 문서에만 쓰세요. 만들어진 스킬을 남에게 공유하면 저작권 문제가 됩니다.
- 변환 시간은 페이지당 수 초입니다. 200페이지면 몇 분 걸립니다.
참고 깃허브: github.com/virgiliojr94/book-to-skill

---

### 3. 자료조사·요약
#### 3-1. 웹 검색 (공식) — 최신 정보 바로
클로드 웹(앱) 기본 기능입니다. 설치 없음.
클로드는 학습이 끝난 시점 이후의 정보를 모릅니다. 그냥 물어보면 옛날 지식으로 답하는 경우가 있어서, 최신 정보가 필요할 때는 요청 맨 앞에 오늘 날짜를 붙이고 "웹 검색해서 알려줘"를 꼭 넣으세요.

ex)
오늘은 2026년 10월 24일이야. 이 날짜 기준으로 클로드 요금제를 웹 검색해서 표로 정리해줘.
오늘은 2026년 10월 24일이야. 이번 주 AI 업계 주요 뉴스를 웹 검색해서 5개만 출처 링크와 같이 알려줘.
오늘은 2026년 10월 24일이야. 이 글에 틀린 정보가 없는지 최신 기준으로 웹 검색해서 확인해줘.


왜 날짜를 붙이나요?
"최신"이라고만 하면 클로드가 자기 학습 시점을 최신으로 착각할 수 있습니다. 날짜를 박아두면 그 시점 기준으로 검색합니다.

---

#### 3-2. Research (공식) — 여러 출처 심층 조사
뭘 해주나요? 클로드가 수십~수백 개 웹 자료를 스스로 조사해 출처 달린 보고서를 만드는 기능.
어디서? 입력창의 리서치(Research) 버튼을 켜고 질문. Pro 이상.
```
국내 소규모 카페의 2026년 하반기 마케팅 트렌드를 조사해서 보고서로 정리해줘. 출처를 꼭 붙여줘.
```
- 일반 검색보다 사용량을 훨씬 많이 씁니다. 보고서·시장조사·비교처럼 깊게 봐야 할 때만 켜세요.

---

#### 3-3. Firecrawl — 사이트 내용을 표로
뭘 해주나요?
사이트 주소를 주면 클로드가 그 페이지 내용을 읽어와서 표로 정리해줍니다. 경쟁사 가격 비교, 채용공고 모아보기 같은 데 씁니다.
필요한 것 Node.js 22 이상 · firecrawl.dev 계정 (무료 티어 있음)
설치 방법 A — CLI (한 줄) ⚫ 일반 터미널
```
npx -y firecrawl-cli@latest init --all --browser
```
브라우저가 열리면 로그인·인증 안내를 따르세요. 끝나면 클로드코드를 껐다 켭니다.
확인: `firecrawl --status`
설치 방법 B — MCP (API 키 직접 넣기) ⚫ 일반 터미널
1. firecrawl.dev 접속 → 무료 가입 → 대시보드 API Keys → 키 복사
1. `발급받은키` 자리에 넣고 실행
```
claude mcp add -s user firecrawl --env FIRECRAWL_API_KEY="발급받은키" -- npx -y firecrawl-mcp
```
바로 써보기
```
아래 세 사이트의 요금제를 읽어와서 가격, 포함 기능을 표로 비교해줘.
(주소 1) (주소 2) (주소 3)
```
```
경쟁사 홈페이지 분석해줘. 어떤 서비스를 얼마에 파는지, 어떤 문구를 쓰는지: (URL)
```
주의
- 로그인해야 보이는 페이지, 봇 접근을 막아둔 사이트(네이버 블로그·카페 등 일부 국내 사이트)는 안 될 수 있어요. 회사 홈페이지, 쇼핑몰, 해외 사이트, 뉴스·블로그 대부분은 잘 됩니다.
- 첫 테스트는 네이버 블로그 말고 일반 홈페이지로 해보세요.
참고 깃허브: github.com/firecrawl/firecrawl

---

#### 3-4. chrome-devtools-mcp — 브라우저 직접 조작
뭘 해주나요?
클로드가 크롬 브라우저를 직접 열어서 페이지를 보고, 어디가 느린지, 무슨 에러가 나는지 찾아줍니다. 내가 만들고 있는 사이트라면 원인을 찾은 뒤 코드까지 고쳐줍니다. 구글 크롬 개발자도구 팀이 만든 공식 도구입니다.
필요한 것 Node.js · 구글 크롬 최신 버전
설치 🟠 클로드코드를 켜고 아래 문장을 그대로 붙여넣으세요. 클로드가 알아서 설치합니다.
```
아래 명령을 그대로 실행해서 chrome-devtools MCP를 설치해줘.

claude mcp add chrome-devtools --scope user npx chrome-devtools-mcp@latest

끝나면 설치됐는지 확인해줘.
```
설치 후 클로드코드를 껐다가 다시 켜세요. `/mcp`를 쳤을 때 chrome-devtools가 connected로 나오면 성공입니다.
(직접 하려면 ⚫ 일반 터미널에서 `claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest`)
바로 써보기 처음 실행하면 크롬 창이 새로 하나 뜨는데, 정상입니다.
```
(내 블로그 주소) 를 열어서 성능을 점검해줘. 느린 원인을 3개만 쉬운 말로 알려줘.
```
내가 만들고 있는 사이트라면:
```
http://localhost:3000 을 열어서 콘솔 에러를 확인하고, 원인을 찾아서 코드를 고쳐줘.
```
활용 예시
주의
- 클로드가 브라우저 화면의 내용을 볼 수 있습니다. 로그인 정보나 개인정보가 떠 있는 페이지에서는 쓰지 마세요.
- 남의 사이트는 점검만 됩니다. 고칠 수 있는 건 내 코드뿐입니다.
- 대형 포털 사이트는 자동 접속을 막거나 결과가 복잡하게 나올 수 있어서, 블로그나 개인 사이트로 시작하는 걸 권합니다.
- MCP는 스킬보다 컨텍스트를 많이 차지합니다. 실제로 쓰는 프로젝트에서만 연결하는 걸 추천합니다.
참고 깃허브: github.com/ChromeDevTools/chrome-devtools-mcp

---

#### 3-5. Claude in Chrome (공식) — 내 크롬에서 바로
뭘 해주나요?
내가 쓰는 크롬 브라우저에 클로드를 붙입니다. 클로드가 크롬 창을 직접 열고 스크롤하고 클릭하면서 페이지를 확인·조작합니다
설치
1. 크롬 웹스토어에서 "Claude" 검색 → Anthropic 공식 확장 설치 → 클로드 계정 로그인
1. 조건 3가지: ①클로드 유료 플랜 ②확장 프로그램에 로그인 ③클로드코드에서 쓸 거면 클로드코드도 `/login`으로 로그인된 상태
실행 중 로그인 페이지나 보안문자를 만나면 클로드가 멈추고 직접 처리해달라고 요청해요.



바로 써보기 🟠 클로드코드에서 (내 사이트가 localhost에 떠 있는 상태)
```
크롬으로 http://localhost:3000/index.html 열어서
스크롤을 천천히 내리면서 모든 섹션을 확인하고,
콘솔 에러랑 깨진 요소가 있으면 찾아서 코드 고쳐줘.
고친 다음 다시 열어서 확인해줘.
```
크롬 창이 뜨고, 주소창 아래 "'Claude'에서 이 브라우저에 대한 디버깅을 시작함" 배너가 뜹니다.
클로드가 스스로 스크롤 내리고, 문제를 찾고, 여러분 컴퓨터의 코드 파일을 직접 고칩니다.
> 💡 이때 고쳐지는 건 내 컴퓨터의 로컬 파일입니다. 인터넷에 배포해둔 사이트가 있다면 고친 뒤 다시 배포해야 반영돼요.
> ⚠️ "플레이라이트(playwright)로 해줘"라고 하지 마세요. 그 단어를 쓰면 클로드가 화면 없이 백그라운드로 돌아가는 도구를 골라서, 브라우저 움직이는 모습이 안 보입니다. 그냥 "크롬으로 열어서 ~해줘"라고 하면 됩니다.
활용 예시
- "회원가입 버튼 눌렀을 때 잘 작동하는지 테스트해줘"
- "페이지에서 깨진 링크 전부 찾아줘"

---

### 4. 웹 디자인
> 4-2 번 부터는  이미 클로드코드로 만든 사이트가 있는 상태에서 시작합니다. 

아직 사이트가 없는 상태이면 4-1을 통해 기본 사이트를 만드시고 그 다음부터 4-2 적용해보시면됩니다.
Node.js 22.20 이상 필요. 버전이 낮으면 클로드코드에 "Node.js 22 이상으로 설치해줘"라고 시키면 됩니다.
#### 4-1. UI UX Pro Max — 업종만 말하면 디자인
뭘 해주나요?
"뷰티샵 홈페이지 만들어줘"처럼 업종만 말하면, 그 업종에 맞는 색·글꼴·레이아웃·피해야 할 것을 클로드가 먼저 정하고 만듭니다. 디자인 감각 없어도 됩니다. 아직 사이트가 없는 분은 이걸로 먼저 만들고 아래 4개로 다듬으세요.
필요한 것 파이썬 3
설치 🟠 클로드코드 안에서, 반드시 한 줄씩 따로
> ⚠️ 여기서 제일 많이 막힙니다. 두 줄을 한꺼번에 붙여넣으면 "not a valid GitHub owner/repo" 에러가 나고, 순서를 바꾸면 "Marketplace not found" 에러가 납니다.
```
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
```
완료 메시지를 확인한 다음:
```
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
```
설치 범위를 물어보면 맨 위 "Install for you (user scope)" 를 선택하고 엔터.
바로 써보기
```
UI UX Pro Max 스킬을 사용해서
네일샵 소개 홈페이지를 한 페이지로 만들어줘.
예약 버튼도 넣어줘.
```
더 화려하게 뽑고 싶으면 (영어 프롬프트가 매칭 정확도가 높습니다. 한글 문구는 어차피 한글로 나옵니다)
이런 영어프롬프트는 클로드에 접속해서,  한글프롬프트 입력 후, “클로드코드에 적용할 영어 프롬프트로 변환해줘” 라는 문구를 추가하시면 영문프롬프트를 얻을 수 있습니다.
```
Build a complete landing page for [an online course that teaches AI automation to office workers].

Requirements:
- Single index.html file, HTML + Tailwind (CDN)
- Korean copy, friendly conversational tone, fully written out (no lorem ipsum)
- No image files. Use CSS gradients, inline SVG icons, and shapes only
- Scroll-triggered reveal animations
- Run the pre-delivery checklist before finishing
```
결과 확인   파일을 더블클릭으로 열지 말고. 클로드코드에 이렇게 시키면 됩니다:
```
이 폴더에서 백그라운드로 npx serve 실행하고 접속 주소 알려줘
```
→ "백그라운드로"라는 말을 꼭 넣으세요. 알려주는 주소(보통 http://localhost:3000)를 크롬에 붙여넣으면 완성된 페이지가 뜹니다.
수정할 때 꼭 이렇게
```
[바꾸고 싶은 것]만 수정해줘. 레이아웃, 애니메이션, 색상 구조는 절대 건드리지 마.
```
이 문장이 없으면 클로드가 구조까지 다시 짜다가 마음에 들었던 디자인을 날려버립니다.
참고 깃허브: github.com/nextlevelbuilder/ui-ux-pro-max-skill

---

#### 4-2. Taste Skill — 디자인 방향부터 잡기
사이트에 맞는 스타일을 먼저 정하고, 실제 이미지까지 넣어서 브라우저로 확인하며 고칩니다.
저장소 이름은 taste-skill이지만 실제 스킬 이름은 design-taste-frontend입니다.
필요한 것 Node.js 22 이상 · 크롬 또는 엣지 (브라우저로 화면 확인)
설치 ⚫ 일반 터미널 (프로젝트 폴더에서)
```
npx skills add Leonxlnx/taste-skill --skill design-taste-frontend -a claude-code -y
```
프롬프트 🟠 클로드코드에 입력
```
design-taste-frontend 스킬을 사용해서 메인 페이지 디자인 방향 잡고 다시 만들어줘. 마케팅 회사 소개 사이트야. 방향 몇 개 제안하고 내가 고르면 진행해.
```
확인할 것: 스타일 방향이 제안되고, 이미지가 들어가고, 브라우저로 확인하며 수정하는 흐름이 나오는지
참고 깃허브: github.com/Leonxlnx/taste-skill

---

#### 4-3. Impeccable — 내 사이트 쓴소리 평가
설치 ⚫ 일반 터미널 (프로젝트 폴더에서)
```
npx impeccable install
```
어느 도구에 붙일지 물어보면 Claude Code, 설치 범위는 현재 프로젝트 선택.
설치 후 클로드코드를 다시 엽니다.
프롬프트 1 — 초기 설정 (처음 한 번) 🟠
```
/impeccable init
```
구버전은 설치된 스킬 안내에 따라 `/impeccable teach`를 사용 (`teach`는 `init`의 별칭)
프롬프트 2 — 평가
```
/impeccable critique
```
→ 시선 흐름, 정보 전달, 아쉬운 디자인을 항목별로 평가
프롬프트 3 — 마무리
```
/impeccable polish
```
→ 최종 품질과 디자인 일관성을 다듬음. critique 결과 중 반영할 항목을 먼저 말해주고 실행하면 정확함
필요할 때만
- `/impeccable typeset` — 글꼴·글자 크기 위계
- `/impeccable layout` — 배치와 여백
- `/impeccable animate` — 모션 추가
참고 깃허브: github.com/pbakaus/impeccable

---

#### 4-4. apple-design — 애플 느낌 모션·글꼴
애플 WWDC 디자인 토크를 웹용으로 번역한 스킬. 인터랙션 반응, 자연스러운 모션, 타이포그래피를 애플식으로 바꿉니다.
설치 ⚫ 일반 터미널 (apple-design만)
```
npx skills@latest add emilkowalski/skills --skill apple-design -a claude-code -y
```
프롬프트 🟠
```
apple-design 스킬 써서 이 사이트 애플 느낌으로 다듬어줘. 페이지 구조는 유지하고 글꼴 위계, 버튼 반응, 페이지 전환 모션을 손봐줘.
```
확인할 것: 요청한 구조가 유지되는지, 버튼 반응과 전환 모션이 자연스러운지, 글자가 읽기 편한지
추가로 설치해볼 스킬 (같은 저장소, `--skill` 이름만 바꾸면 됨)
- `prototype` — 한 UI를 여러 버전으로 뽑아서 비교
- `review-animations` — 애니메이션 엄격 검토
- `break-ui` — 최악 데이터로 UI 깨뜨려보기
참고 깃허브: github.com/emilkowalski/skills

---

#### 4-5. Web Design Guidelines — 100개 기준 UI 자동 검수
접근성, 폼, 이미지, 모바일 터치까지 웹 UI 코드를 점검하고 문제점을 짚어줍니다.
설치 ⚫ 일반 터미널
```
npx skills add vercel-labs/agent-skills --skill web-design-guidelines -a claude-code -y
```
프롬프트 🟠
```
web-design-guidelines로 이 사이트 전체 검수해줘. 특히 문의 폼이랑 모바일. 검토한 파일과 근거를 같이 보여주고, 고칠 수 있는 건 바로 고쳐.
```
확인할 것: 지적 개수보다 어떤 파일·범위를 봤는지와 지적 근거가 제시됐는지. 문제가 없으면 지적이 없을 수도 있음
⚠️ 코드 기준 점검입니다. 문의 폼이 실제로 접수되는지는 직접 눌러봐야 함
참고 깃허브: github.com/vercel-labs/agent-skills

---

#### 4번 칸 설치 확인
```
npx skills list
```
`design-taste-frontend`, `apple-design`, `web-design-guidelines`가 목록에 있으면 OK → 클로드코드 재시작 → 프롬프트로 호출해보기
Impeccable은 `/impeccable init`으로 초기 설정이 진행되면 정상입니다.
UI UX Pro Max는 `/plugin` → Installed에 보이면 OK.
- `npx skills`가 어느 도구에 설치할지 물어보면 → Claude Code 선택
- 스킬 깔았는데 클로드코드가 못 찾으면 → 클로드코드 껐다 켜기
- 비교 테스트 중엔 프롬프트에 사용할 스킬 이름을 명시. 결과가 섞이면 폴더 복사본이나 새 세션에서 따로 비교

---

### 5. 마케팅·SNS
#### 5-1. Agency Agents — 마케팅·영업 담당 AI 팀
뭘 해주나요?
매번 "너는 마케팅 전문가야"라고 길게 설명할 필요 없이, 역할과 일하는 방식이 미리 정리된 AI 담당자를 불러서 씁니다. 마케팅, 영업, 기획, 디자인 등 230개 이상이 들어 있습니다.
설치 🟠 클로드코드에 아래 글을 그대로 붙여넣으세요. 클로드가 알아서 설치합니다.
```
https://github.com/msitarzewski/agency-agents 를 받아서
마케팅(marketing), 영업(sales), 기획(product), 디자인(design) 부서의 에이전트 파일만
내 사용자 폴더의 .claude/agents 폴더에 복사해줘. (어느 폴더에서든 쓰게 전역으로)
기존에 같은 이름의 파일이 있으면 덮어쓰지 말고 알려줘.
설치가 끝나면 각 에이전트 파일의 name 값과 역할을 한국어로 정리해줘.
```
끝나면 클로드코드를 껐다 켜세요.
왜 4개 부서만 깔까요? 230개를 전부 깔면 목록이 너무 길어져서 오히려 찾기 어렵습니다. 더 필요하면 위 글에서 부서 이름만 바꿔서 다시 붙여넣으면 됩니다.
바로 써보기 담당자 이름은 설치가 끝났을 때 클로드가 정리해 준 name 값을 그대로 쓰세요.
```
Instagram Curator 에이전트를 서브에이전트로 호출해서
우리 브랜드의 일주일 인스타 콘텐츠를 기획해줘.
브랜드: 성수동 수제 디저트 가게
```
활용 예시
팁 어떤 담당자가 있는지 모르겠으면 "설치된 에이전트 목록을 한국어 설명과 같이 보여줘"라고 물어보세요.
참고 깃허브: github.com/msitarzewski/agency-agents · 무료 (MIT)

---

#### 5-2. marketingskills — 카피·전환율·광고 40여 개 스킬
뭘 해주나요?
마케터가 매일 하는 일을 스킬로 쪼개 놓은 모음입니다. 카피라이팅, 전환율 개선(CRO), SEO, 광고 문구, 이메일 시퀀스, 경쟁사 분석 등 40여 개. Agency Agents가 "담당자"라면 이건 "담당자가 쓰는 업무 매뉴얼"입니다.
설치 ⚫ 일반 터미널 — 전부 깔기
```
npx skills add coreyhaines31/marketingskills -a claude-code -g
```
필요한 것만 깔기 (처음엔 이쪽 추천):
```
npx skills add coreyhaines31/marketingskills --skill copywriting --skill cro --skill ai-seo -a claude-code -g
```
끝나면 클로드코드에서 `/reload-plugins`
바로 써보기 🟠
```
copywriting 스킬로 우리 가게 랜딩페이지 첫 화면 문구를 써줘.
가게: 성수동 수제 디저트, 핵심 메시지: 매일 아침 직접 굽는다.
```
```
cro 스킬로 이 페이지의 전환율을 높일 수 있는 부분을 찾아서 고쳐줘. (index.html)
```
주의
- 스킬이 많아서 전부 깔면 클로드가 어떤 걸 쓸지 헷갈릴 수 있습니다. 자주 쓰는 3~5개만 깔고 시작하세요.
- 영어 기준으로 만들어진 스킬이라 결과를 한국 시장에 맞게 한 번 더 다듬으세요.
참고 깃허브: github.com/coreyhaines31/marketingskills

---

#### 5-3. claude-seo — 사이트 SEO 진단·수정
뭘 해주나요?
내 사이트 주소를 주면 SEO(검색 노출) 관점에서 80개 넘는 항목을 점검하고, 고칠 코드까지 줍니다. AI 검색(GEO)에 잘 걸리게 하는 점검도 들어 있습니다. 25개 세부 스킬 + 18개 에이전트로 구성된 큰 도구입니다.
필요한 것 파이썬 3.11 이상 · 클로드코드
설치 🟠 클로드코드 입력창에 한 줄씩
```
/plugin marketplace add AgriciDaniel/claude-seo
```
```
/plugin install claude-seo@agricidaniel-claude-seo
```



바로 써보기

사이트 전체 점검. 에이전트 여러 개가 동시에 돌아서 10~15분 걸립니다.
```
/claude-seo:seo audit https://내사이트주소


```
```

이 사이트의 SEO 점검 결과 중 바로 고칠 수 있는 것부터 코드로 고쳐줘.


```
/claude-seo:seo page [https://내사이트주소/about](https://xn--220bw61ahtai7p93bg1z/about)
→ 페이지 하나만 깊게 
자주 쓰는 명령
주의
- 점검 결과가 "순위 상승"을 보장하지는 않습니다. 기본기를 빠짐없이 챙기는 용도입니다.
- 남의 사이트는 점검만 되고, 고칠 수 있는 건 내 코드뿐입니다.
참고 깃허브: github.com/AgriciDaniel/claude-seo · 무료 (MIT)

---

#### 5-4. claude-blog — 블로그 글 제작
뭘 해주나요?
claude-seo 만든 사람이 만든 블로그 글쓰기 도구입니다. 주제를 주면 검색 노출을 고려한 구조로 글을 짜고, 제목·소제목·본문까지 씁니다.
필요한 것 파이썬 3.11 이상 · 클로드코드
설치 🟠 한 줄씩
```
/plugin marketplace add AgriciDaniel/claude-blog
```
```
/plugin install claude-blog@agricidaniel-claude-blog
```
바로 써보기
```
claude-blog로 "직장인 AI 활용 입문" 주제 블로그 글을 써줘. 한국어로, 2000자 안팎, 소제목 4개.
```
참고 깃허브: github.com/AgriciDaniel/claude-blog · 무료 (MIT)

---

#### 5-5. clone-website — 잘된 사이트 벤치마킹
뭘 해주나요?
마음에 드는 사이트의 분위기(색·배치·글꼴·움직임)를 가져와서 내 페이지를 만듭니다. 참고 사이트를 똑같이 복사하는 게 아니라, 분위기만 가져오고 이름·글·사진은 전부 내 것으로 새로 만듭니다.
방법 1. 캡처 + 프롬프트 (설치 없음, 추천) 
아래 페이지에 상세가이드 정리해두었습니다.
[https://grand-count-2aa.notion.site/3f550157ef1380febf3fc4dd35686e15?pvs=74](https://grand-count-2aa.notion.site/3f550157ef1380febf3fc4dd35686e15?pvs=74)
방법 2. clone-website 스킬 (더 정밀하게, 개발자용)
사이트 주소만 주면 디자인 토큰(색·글꼴·간격)을 뽑아서 Next.js 프로젝트로 다시 만들어 주는 스킬입니다. 결과물이 Next.js라 방법 1보다 다루기 어렵습니다. 개발을 아는 분만 쓰세요. 방법1 방식을 추천합니다


필요한 것
- Node.js 24 이상 (`node -v`로 확인. 낮으면 클로드코드에 “Node.js 24 이상으로 올려줘”)
- 클로드가 브라우저를 쓸 수 있어야 합니다 → 3-4 chrome-devtools-mcp 또는 3-5 Claude in Chrome 중 하나를 먼저 연결
- 모델은 Opus 권장 (`/model`로 변경)
설치 🟠 클로드코드를 켜고 아래를 그대로 붙여넣으세요. 어디에 만들지 물어보면 폴더 위치를 알려주면 됩니다.
```
https://github.com/JCodesMore/ai-website-cloner-template 를
내 컴퓨터의 새 폴더에 독립 프로젝트로 설치해줘.
어디에 만들지 나한테 물어봐. 저장소를 받고, origin 리모트를 지우고,
의존성을 설치하고, npm run check 를 실행해줘.
끝나면 바로 사이트를 복제할 수 있는 상태로 두고 폴더 위치를 알려줘.
```

끝나면 그 폴더에서 클로드코드를 다시 켭니다. (스킬이 프로젝트 안에 들어 있어서 다른 폴더에선 `/clone-website`가 안 뜹니다)

저같은 경우 C:\claudeCode\bback\ai-website-cloner 
경로가 나와서 새로운 터미널을 킨 다음에 cd C:\claudeCode\bback\ai-website-cloner  실행후 여기서 claude 실행했습니다




바로 써보기 🟠
txt
```
/clone-website https://참고사이트주소
```




주의 
남의 사이트 글·사진·로고를 그대로 쓰면 안 됩니다. 분위기만 참고하세요.
`/clone-website`가 “Unknown command”면 프로젝트 폴더 밖에서 켠 겁니다. 설치된 폴더로 이동해서 다시 켜세요.




참고 깃허브: github.com/JCodesMore/ai-website-cloner-template

---

### 6. 영상·음성
#### 6-1. hyperframes — 말로 시키면 모션 영상
뭘 해주나요?
"10초짜리 인트로 영상 만들어줘"라고 말하면, 클로드가 영상을 짜고 mp4 파일로 뽑아줍니다. 릴스에서 제 머리 위에 떠 있던 영상이 이걸로 만든 겁니다. 영상 편집 프로그램을 몰라도 됩니다.
필요한 것 Node.js 22 이상 · FFmpeg
FFmpeg 설치 ⚫ (윈도우)
```
winget install Gyan.FFmpeg
```
맥: `brew install ffmpeg`. 설치 후 VS Code를 껐다 켜고 `ffmpeg -version`으로 확인.
설치 (제일 쉬운 방법) 🟠 클로드코드에 그대로 붙여넣기
```
hyperframes 스킬을 설치해줘: https://github.com/heygen-com/hyperframes
README의 설치 방법(npx skills add heygen-com/hyperframes)을 따르고, Node.js 22 이상과 FFmpeg가 있는지 확인해서 없으면 설치해줘.
```
설치 (직접 하는 방법) ⚫ 일반 터미널
```
npx skills add heygen-com/hyperframes -a claude-code -g
```
- 선택 화면이 뜨면 Core Skills 묶음에 방향키로 가서 스페이스로 체크 → 엔터.
- 끝나면 클로드코드에서 `/reload-plugins`
바로 써보기 🟠
```
/hyperframes 로 10초짜리 세로 영상(1080x1920) 만들어줘.
검은 배경에 "2026년 10월" 글자가 서서히 나타나고, 그 아래 별 3개가 차례로 튀어나오게.
다 되면 mp4로 렌더해줘.
```
끝나면 클로드가 mp4 파일 위치를 알려줍니다. 마음에 안 들면 "글자 더 크게", "별을 노란색으로"처럼 말로 고치면 됩니다.
활용 예시
팁 한 번에 긴 영상보다 짧은 영상 여러 개로 나눠 시키는 게 결과가 좋습니다.
주의 렌더는 내 컴퓨터에서 돌아갑니다. 환경 문제가 의심되면 터미널에서 `npx hyperframes doctor`를 치면 뭐가 빠졌는지 알려줍니다.
참고 깃허브: github.com/heygen-com/hyperframes · 무료 (Apache 2.0)

---

#### 6-2. reels-scripting — 떡상 릴스 역분석해 대본
뭘 해주나요?
떡상한 릴스를 역분석해서 내 소재로 새 대본을 쓰는 스킬입니다. 훅의 첫 마디와 단어 수, "너 vs 나" 화법 비율, 문장 길이, 스크롤을 멈추게 하는 요소까지 뜯어보는 분석 프레임워크가 내장돼 있습니다. 팔로워 35만 크리에이터가 실제로 쓰는 콘텐츠 시스템(social-media-skills) 중 하나입니다.
설치 — 클로드코드 ⚫ 일반 터미널
```
git clone https://github.com/charlie947/social-media-skills.git
cp -r social-media-skills/skills/reels-scripting ~/.claude/skills/
```
끝나면 클로드코드에서 `/reload-plugins`
(같은 저장소에 hook-generator(훅 6개 즉시 생성), post-scorer(올리기 전 점수), voice-builder(내 말투 학습)가 같이 있습니다. 쓸 거면 `cp` 줄의 폴더 이름만 바꿔서 추가하세요. voice-builder를 먼저 돌려두면 다른 스킬이 내 말투 파일을 읽고 작업합니다.)
바로 써보기 🟠
```
reels-scripting 스킬로 이 릴스 대본을 역분석하고, 같은 구조로 "직장인 AI 부업" 주제 대본을 써줘.
(참고 릴스 대본이나 자막 붙여넣기)
```
참고 깃허브: github.com/charlie947/social-media-skills

---

#### 6-3. Higgsfield MCP — 영상 AI 바로 연결
뭘 해주나요?
Seedance, Kling, 나노바나나… 요즘 잘나가는 이미지·영상 AI를 하나씩 다 구독하기 부담되죠? Higgsfield를 연결하면 최상위 모델들을 클로드 채팅 안에서 골라 쓸 수 있어요.
필요한 것 higgsfield.ai 계정 + 유료 구독 (플랜 크레딧으로 생성)
설치
STEP 1. higgsfield.ai 접속 → 회원가입
```
claude mcp add -s user --transport http higgsfield https://mcp.higgsfield.ai/mcp
```
STEP 3. 처음 사용할 때 브라우저 로그인 창이 뜨면 → 로그인하고 승인을 누르면 끝!
💰 과금 안내 Higgsfield는 월 구독제예요 (기본 플랜 월 $15 수준부터). 구독하면 크레딧을 받고, 이미지·영상을 만들 때마다 크레딧이 차감되는 방식입니다. MCP로 연결해도 별도 결제가 아니라 내 계정 크레딧을 그대로 씁니다.
바로 써보기
- "내 제품 사진인데, 광고용 이미지 컨셉 3가지로 만들어줘"
- "이 이미지로 15초짜리 릴스용 영상 만들어줘"
- "방금 만든 이미지 배경만 바꿔줘"
✅ 연결 확인 "간단한 테스트 이미지 하나 만들어줘"가 되면 성공!

---

#### 6-4. ElevenLabs MCP — 나레이션·음성 생성
<!-- ⚠ 신규 — 기존 가이드 없음. ElevenLabs 공식 MCP(github.com/elevenlabs/elevenlabs-mcp) 기준. 직접 테스트 전. API 키·크레딧 필요 -->
뭘 해주나요?
클로드 안에서 글을 음성으로 바꿉니다. 릴스 나레이션, 안내 음성, 내 목소리 복제(유료)까지. ElevenLabs가 공식으로 만든 MCP입니다.
필요한 것 파이썬 · uv (파이썬 도구 설치 프로그램) · ElevenLabs API 키 (elevenlabs.io 가입 → Profile → API Keys, 무료 티어 있음)
```
claude mcp add -s user elevenlabs --env ELEVENLABS_API_KEY=내API키 -- uvx elevenlabs-mcp
```
uv가 없다고 나오면 클로드코드에 "uv 설치해줘"라고 시키세요. 윈도우는 설치 후 터미널을 새로 여세요.
바로 써보기 🟠
```
아래 문장을 차분한 여성 목소리로 음성 파일로 만들어줘. 한국어.
"클로드 도구 50개, 내 분야 것만 깔면 됩니다."
```
```
이 음성 파일을 글로 받아 적어줘. (파일 경로)
```
주의
- 음성 생성마다 ElevenLabs 크레딧이 차감됩니다. 무료 티어는 월 글자 수 제한이 있습니다.
- API 키는 명령어에 들어가니 캡처 올릴 때 가리세요.
참고 깃허브: github.com/elevenlabs/elevenlabs-mcp (공식)

---

#### 6-5. youtube-transcript — 영상 대본 뽑기
뭘 해주나요?
유튜브 주소를 주면 자막(대본)을 시간 표시와 함께 글로 뽑아줍니다. 1시간짜리 강의 영상을 안 보고 요약하거나, 내 영상 대본을 글로 정리해 블로그로 쓸 때.
필요한 것 Node.js
설치 
```
npx skills add badlogic/pi-skills --skill youtube-transcript -a claude-code -g
```
끝나면 클로드코드에서 `/reload-plugins`
바로 써보기 🟠
```
youtube-transcript로 이 영상 대본 뽑아서 핵심 5줄로 요약해줘.
https://www.youtube.com/watch?v=영상ID
```
주의
- 자막이 없는 영상은 안 됩니다. 자동 생성 자막은 오타가 있을 수 있습니다.
- 남의 영상 대본을 그대로 재배포하지 마세요. 요약·학습용으로만.
참고 깃허브: github.com/badlogic/pi-skills

---

### 7. 앱 연결·MCP (공식 커넥터)
연결해 두면 클로드가 내 메일, 일정, 파일, 노션을 직접 읽고 처리합니다. 복사해서 붙여넣던 일이 사라져요. 설치가 아니라 로그인입니다. 무료.
연결 방법 (클로드 웹/앱)
1. 왼쪽 메뉴에서 사용자 지정(Customize) 으로 들어갑니다
1. 커넥터(Connectors) 를 누릅니다
1. 연결할 앱 이름을 검색하고 연결 을 누릅니다
1. 그 앱의 로그인 화면이 뜨면 승인합니다
(메뉴 이름이 다르면 설정(Settings) → 커넥터(Connectors) 에서 찾으세요)
#### 7-1. Gmail — 답장 초안
```
어제 온 메일 중에 답장이 필요한 것만 골라서, 각각 답장 초안을 써줘.
```
```
내 Gmail에서 이번 주 안 읽은 메일 중 중요한 것만 요약해줘.
```
#### 7-2. Google Drive — 파일 찾기·요약
```
드라이브에서 '10월 기획서' 파일을 찾아서 핵심만 5줄로 요약해줘.
```
#### 7-3. Notion — 문서 정리
```
방금 정리한 회의록을 노션에 새 페이지로 올려줘.
```
```
노션에서 지난달 콘텐츠 아이디어 적어둔 페이지 찾아줘
```
✅ 연결 확인 "내 노션에 어떤 페이지 있는지 3개만 보여줘"라고 물어보세요. 목록이 나오면 성공!
#### 7-4. Slack — 메시지 요약
```
슬랙 #마케팅 채널에서 이번 주 결정된 사항만 정리해줘.
```
#### 7-5. GitHub — 코드 저장소 연결
```
내 GitHub 저장소 목록을 보여주고, 최근에 수정된 것부터 정리해줘.
```
클로드코드에서는 `/mcp`나 `claude mcp add`로도 연결할 수 있지만, 처음엔 클로드 앱 커넥터가 제일 쉽습니다.
💡 안 쓰는 커넥터는 꺼두세요. 같은 화면에서 끄고 켤 수 있습니다.

---

### 8. 자동화

해당 자동화관련내용은 아래 페이지를 참고하시면 좀더 상세하게 
확인가능합니다.

상세가이드
 https://grand-count-2aa.notion.site/3a850157ef1380f6a4cad8b5b8c943a6
#### 8-1. 예약 작업 (공식) — "매주 월 9시에 정리해줘"
클로드 웹(앱)에서 (Pro 이상)
매주, 매일 반복되는 일은 한 번만 말해두면 됩니다.
```
매주 월요일 오전 9시(한국 시간)에 지난주 받은 메일을 확인해서,
답장이 필요한 것과 이번 주 할 일을 정리해줘.
```
- 말만 해서는 등록이 끝나지 않습니다. 클로드가 제안한 작업 이름·시간·내용을 확인하고 예약(Schedule) 버튼을 눌러야 등록됩니다.
- 왼쪽 메뉴의 예약됨(Scheduled) 목록에 보이면 완료입니다. 수정과 삭제도 여기서 합니다.
- 예약된 일은 클라우드에서 돌아가서 내 기기가 전부 꺼져 있어도 실행됩니다.
- 단, 내 PC의 파일이 필요한 예약은 PC와 데스크톱 앱이 켜져 있을 때만 실행됩니다.
클로드코드 루틴으로 (매일 아침 슬랙 브리핑 예시)
1. 클로드 앱 → 클로드코드(Code) 메뉴 → 루틴(Routines) 에서 새 루틴 생성
1. 연결할 저장소 선택 — 없으면 GitHub에서 빈 저장소 하나 만들어 연결 (이름은 `my-routine` 추천)
1. 루틴 설정에서 Slack 커넥터 연결 → 슬랙 로그인 → 워크스페이스 승인
1. 지침 작성:
```
매일 아침 AI 뉴스 모닝 브리핑을 만들어줘.

1. 최근 24시간 내 [AI 업계] 주요 뉴스를 검색해서 5개 골라줘
2. 각 뉴스를 2~3문장으로 요약하고, 왜 중요한지 한 줄 코멘트 달아줘
3. 전체를 보기 좋게 정리해서 슬랙 [#ai-브리핑] 채널로 보내줘
```
1. 트리거를 매일 오전 7시 같은 원하는 시각으로 설정하면 끝

---

#### 8-2. 자동 모드 — 안 묻고 끝까지 알아서 진행
뭘 해주나요?
클로드코드 기본 기능입니다. 설치 없음. 기본 상태에서는 클로드가 파일을 만들거나 수정할 때마다 "이 작업을 허용하시겠습니까?" 하고 물어봅니다. 한두 번은 괜찮은데 개발 내내 이러면 자리를 비울 수가 없죠. 자동 모드를 켜면 일일이 물어보지 않고, 끝날 때까지 알아서 진행한 뒤 완료 보고를 해줍니다.
켜는 법 🟠 클로드코드 입력창에서 키보드 Shift + Tab




화면 하단에 `auto mode on`이라고 뜨면 성공입니다. (버전에 따라 `auto` / `accept edits` 등으로 표시될 수 있습니다. 매번 "수정할까요?" 묻지 않는 상태로 두면 됩니다.)
바로 써보기 자동 모드를 켠 뒤:
```
ref 폴더의 캡처를 참고해서 index.html 한 장짜리 소개 페이지를 만들어줘.
나한테 질문하지 말고 끝까지 진행하고, 끝나면 뭘 만들었는지 5줄로 보고해줘.
```
터미널에 글자가 계속 올라가면 정상입니다. 끝나면 "완료" 보고가 뜹니다.
꼭 알아야 할 것 2가지
① 새 프로젝트에서만 켜세요
자동 모드는 확인 없이 파일을 수정합니다. 회사 코드나 오래 만들어온 프로젝트에서 켰다가 클로드가 실수로 뭔가를 갈아엎으면 되돌리기 어렵습니다.
- ✅ 연습용/새로 시작하는 프로젝트 → 켜도 OK
- ❌ 회사 코드, 소중한 기존 프로젝트 → 끄고 쓰세요
② 터미널을 새로 켜면 다시 눌러야 합니다
자동 모드는 켜져 있는 터미널에서만 유지됩니다. VS Code 껐다 켜거나 새 터미널을 열면 꺼져 있으니, 그때마다 Shift + Tab 한 번씩 눌러주세요.
"무조건 다 승인"은 아니에요
클로드가 작업 하나하나를 스스로 검사해서 안전한 작업(코드 작성, 파일 생성 등)은 자동으로 진행하고, 위험한 작업(파일 대량 삭제 등)은 스스로 차단하고 다른 방법을 찾습니다. 다만 잘못된 수정을 완전히 막아주는 건 아니니 중요한 파일이 있는 폴더에서는 꺼두세요.
혹시 중간에 질문이 뜨면 자동 모드여도 가끔 "A / B 중 뭘로 할까요?" 같은 질문이 뜰 수 있습니다. 번호 선택지면 1, "(Recommended)" 표시가 있으면 그걸로, Yes/No면 y.

---

#### 8-3. Composio MCP — 수천 개 앱 한 번에 연결
뭘 해주나요?
Composio는 유튜브·지메일·노션·인스타그램 등 앱 수천 개를 클로드에 한 번에 꽂아주는 무료 서비스입니다. 연결만 해두면 클로드가 여러분 대신 그 앱 안에서 직접 일합니다. 공식 커넥터에 없는 앱도 여기서 연결할 수 있어요.

상세가이드는 아래페이지에서 확인가능합니다.
[https://grand-count-2aa.notion.site/1-000-3b750157ef1380faba1ac0ec44baa7d7](https://grand-count-2aa.notion.site/1-000-3b750157ef1380faba1ac0ec44baa7d7)

시작 전 확인
- 커스텀 커넥터 메뉴는 클로드 유료 플랜에서 열립니다. 설정에 메뉴가 안 보이면 플랜을 먼저 확인하세요.
- PC(웹 또는 데스크톱 앱) 기준으로 설명합니다. 모바일보다 PC에서 설정하는 게 편해요.
STEP 1. Composio 무료 가입
1. composio.dev/foryou 접속
1. 구글 계정으로 가입 (무료, 카드 등록 없음)
1. 가입하면 나오는 연결 링크를 복사해둡니다
```
https://connect.composio.dev/mcp
```
STEP 2. 클로드에 커넥터 추가
1. 클로드 설정 → 커넥터 로 이동
1. 커스텀 커넥터 추가 클릭
1. 이름: `Composio`
1. URL 칸에 STEP 1에서 복사한 링크 붙여넣기
1. 추가 클릭 → 끝!
STEP 3. 쓸 앱 연결하기
1. 커넥터 목록에 Composio가 뜨면 연결 성공
1. Composio에서 Connect Apps 선택 후 연결할 앱 Connect 버튼 클릭 (구글 로그인하듯 클릭 몇 번)
바로 써보기
```
Composio를 통해 내 유튜브 계정에 접속해서
"요즘 주식시장 근황" 검색 결과 중
제일 인기 많은 영상 3개를 내 플레이리스트에 넣어줘
```
```
다음 주 화요일 오후 3시에 미팅 일정 잡아줘
```
```
내 인스타그램 계정 최근 게시물 성과 분석해줘
```
자주 막히는 부분
- 커넥터가 목록에 안 떠요 → 클로드 새로고침 후 다시 확인
- 앱이 반응이 없어요 → Composio 대시보드(composio.dev/foryou)에서 해당 앱 연결 상태 확인 후 재연결
- 커스텀 커넥터 추가 메뉴가 없어요 → 무료 플랜일 가능성이 높아요. 유료 플랜에서 지원되는 기능입니다
참고 composio.dev · 무료 플랜 있음

---

#### 8-4. Playwright MCP — 로그인·입력 같은 웹 작업 대행
뭘 해주나요?
Firecrawl이 웹페이지를 '읽는' 거라면, Playwright는 클로드가 브라우저를 직접 조작하는 거예요. 사이트 접속, 버튼 클릭, 글자 입력을 클로드가 대신합니다. 화면 없이 백그라운드에서 돌아가서 반복 작업에 맞습니다.
필요한 것 없음 🎉
설치 ⚫ 일반 터미널, 이 한 줄만
```
claude mcp add -s user playwright -- npx -y @playwright/mcp@latest
```
바로 써보기
- "○○ 사이트 열어서 첫 화면에 뭐가 떠 있는지 알려줘"
- "이 사이트에서 ○○ 검색하면 뭐가 나오는지 보고 알려줘"
- "이 양식에 아래 내용 입력하고 제출 직전까지 해줘"
✅ 연결 확인 "구글 열어서 오늘 날씨 검색해줘"가 되면 성공!
참고 깃허브: github.com/microsoft/playwright-mcp (공식)

---

#### 8-5. agent-browser — 만든 사이트 자동 테스트
뭘 해주나요? 만든 웹사이트를 클로드가 직접 열어서 버튼·입력창이 잘 되는지 테스트해 줍니다. Playwright MCP가 "남의 사이트에서 작업 대행"이라면, 이건 "내가 만든 사이트 검사" 쪽.
⚫ 이건 일반 터미널에 넣습니다. (클로드코드 입력창 아님)
먼저 확인 한 가지. 아래를 넣었을 때 숫자가 나오면 다음으로 넘어가세요.
```
npm --version
```
설치. 한 줄씩 넣고, 끝날 때까지 기다린 뒤 다음 줄을 넣으세요.
```
npm install -g agent-browser
```
```
agent-browser install
```
```
npx skills add vercel-labs/agent-browser --skill agent-browser -g -a claude-code -y
```
두 번째 줄은 테스트용 크롬을 내려받느라 몇 분 걸릴 수 있어요.
✅ 끝. 클로드코드를 켜고 이렇게 시키면 됩니다. 🟠
주소는 내 사이트 주소로 바꾸세요. 내 컴퓨터에서 만든 사이트라면 먼저 실행해 두고, 그때 화면에 나오는 주소(예: http://localhost:3000)를 넣으면 됩니다.
```
agent-browser로 http://localhost:3000 열어서 버튼이랑 입력창이 잘 되는지 테스트해줘
```

참고: 내가 만든 사이트가 아니어도 접근 가능한 웹사이트는 열어서 테스트할 수 있습니다. 다만 로그인·캡차·문자 인증은 사람이 직접 처리해야 할 수 있으며, 사이트의 접근 제한에 따라 일부 테스트가 불가능할 수 있습니다.






참고 깃허브: github.com/vercel-labs/agent-browser

---

### 9. 개발·코딩
#### 9-1. Context7 — 최신 설명서 보고 코딩
뭘 해주나요?
AI가 옛날 버전 코드를 줘서 에러 나는 문제를 해결합니다. 클로드가 최신 공식문서를 실시간 확인하고 코딩해요. 비개발자일수록 필수.
설치 방법 A — 플러그인 🟠 한 줄씩
```
/plugin marketplace add upstash/context7
```
```
/plugin install context7@context7-marketplace
```
설치 후 로그인을 한 번 해야 합니다.
1. 클로드코드를 껐다가 다시 켭니다
1. 입력창에 `/mcp` 를 입력합니다
1. 목록에서 Context7을 고릅니다
1. 브라우저가 열리면 로그인합니다 (무료)
설치 방법 B — MCP 한 줄 ⚫ 일반 터미널
```
claude mcp add context7 -- npx -y @upstash/context7-mcp
```
바로 써보기 요청 끝에 `use context7`을 붙이면 확실합니다.
```
Next.js로 로그인 페이지 만들어줘. use context7
```
참고 깃허브: github.com/upstash/context7

---

#### 9-2. Ponytail — 꼭 필요한 코드만
뭘 해주나요?
"안 써도 되는 건 안 쓴다"를 클로드에 주입합니다. 만들기 전에 7단계로 자문해요 — 이거 꼭 필요한가? 이미 있는 거 아닌가? 한 줄이면 되지 않나? 제작자 테스트에서 코드량 평균 54%, 비용 약 20% 줄었습니다. (작업과 모델에 따라 달라요)
필요한 것 Node.js (자동 켜짐 기능에 필요)
설치 🟠 클로드코드 입력창에 반드시 한 줄씩 따로
```
/plugin marketplace add DietrichGebert/ponytail
```
```
/plugin install ponytail@ponytail
```
✅ 끝. 다음에 클로드코드를 켤 때부터 알아서 적용됩니다.
`/ponytail`을 입력하면 꺼져 있을 땐 켜지고, 켜져 있을 땐 현재 상태를 보여줍니다. 끄려면 `/ponytail off`.
처음에는 기본 모드 그대로 쓰세요.
참고 깃허브: github.com/DietrichGebert/ponytail

---

#### 9-3. 케이브맨 (Caveman) — 짧게 답해서 토큰 절약
뭘 해주나요? 클로드가 답변할 때 붙이는 부연설명·서론·잡담을 전부 빼고 핵심만 짧게 답하게 만들어 줍니다. 생성되는 코드는 그대로고, 말수만 줄어듭니다. 답변이 짧아지면 토큰 사용이 줄어서 같은 요금제로 훨씬 오래 쓸 수 있습니다.
설치 방법 A — 플러그인 🟠 한 줄씩
```
/plugin marketplace add JuliusBrussee/caveman
```
```
/plugin install caveman@caveman
```
설치 방법 B — 스킬 한 줄 ⚫ 일반 터미널
```
npx skills add JuliusBrussee/caveman -g
```
✅ 끝. 설치하면 알아서 켜질 수 있습니다. 답변이 그대로 길면 직접 켜세요.
켜기
```
/caveman
```
끄기
```
stop caveman
```
끄기는 지금 대화에만 적용됩니다. 아예 안 쓰고 싶으면 `/plugin` 을 입력해서 목록에서 케이브맨을 꺼두면 됩니다.
참고 깃허브: github.com/JuliusBrussee/caveman

---

#### 9-4. code-review (공식) — 짠 코드 자동 검토
뭘 해주나요? 방금 작업한 코드를 클로드가 여러 관점으로 한 번 더 검토해 줍니다. 앤트로픽 공식 플러그인. 바이브코딩 안 하시는 분은 건너뛰어도 됩니다.
설치 🟠 클로드코드 켜고 아래 명령어 복붙
```
/plugin install code-review@claude-plugins-official
```
설치가 끝나면 재시작 한 번 해주시면 적용됩니다.
바로 써보기
```
/code-review
```
→ 방금 작업한 코드를 클로드가 여러 관점으로 한 번 더 검토해줘요.
팁 입력창에 `/` 를 치면 설치된 플러그인의 명령어 목록이 자동으로 떠요. 원하는 명령어를 클릭하면 끝.
참고 깃허브: github.com/anthropics/claude-plugins-official (공식)

---

#### 9-5. gstack — 기획·리뷰·배포 슬래시 명령
뭘 해주나요?
클로드코드로 뭔가 만들 때, 만들기 전에 아이디어를 검토하고 만든 뒤에 문제를 점검하는 일을 명령어 하나로 시킬 수 있게 해줍니다. 실리콘밸리 투자사 와이콤비네이터(YC) 대표 Garry Tan이 자기가 쓰는 세팅을 공개한 것입니다.
이런 분께 맞습니다 클로드코드로 웹사이트나 서비스를 직접 만들고 있는 분. 설치가 손이 많이 갑니다.
윈도우 사용자는 먼저 확인하세요 공식 README는 Windows 11의 Git Bash 또는 WSL 환경을 안내합니다. Windows에서는 Git, Bun 외에 Node.js도 필요합니다. 맥은 Git과 Bun만 있으면 됩니다.
설치 🟠 클로드코드에 아래 글을 그대로 붙여넣으세요.
```
https://github.com/garrytan/gstack 의 공식 설치 안내를 확인하고,
내 사용자 폴더에 전역으로 설치해줘.
1. 내 운영체제와 실행 셸(Git Bash, WSL 등)을 확인해줘.
2. Git과 Bun이 있는지 확인하고, Windows라면 Node.js도 확인해줘.
3. 없는 프로그램은 설치 방법을 안내해줘.
4. 내 사용자 폴더의 .claude/skills/gstack 위치에 받아줘.
   이미 gstack 폴더가 있으면 지우거나 다시 받지 말고, 기존 설치 상태부터 확인해줘.
5. 그 폴더에서 ./setup 을 실행해줘.
6. 설치가 끝나면 브라우저 기능 설치에 실패한 부분이 있는지 알려줘.
중간에 실패하면 고치지 말고, 실패한 단계와 오류 내용을 그대로 알려줘.
```
끝나면 클로드코드를 껐다 켜세요. `/office-hours`를 입력했을 때 명령어가 뜨면 설치된 것입니다.
바로 써보기 아이디어 단계라면 이것부터. 클로드가 질문을 던지면서 아이디어를 정리해 줍니다.
```
/office-hours
```
추천 순서
주의
- `/qa-only`와 `/qa`는 내가 관리하는 개발·테스트 사이트에서만 사용하세요.
- 명령어가 수십 개라 처음엔 위 4개만 쓰세요.
참고 깃허브: github.com/garrytan/gstack · 무료 (MIT)

---

### 10. 고급 활용
#### 10-1. ECC — 에이전트·스킬 수백 개 한 번에
설명
클로드코드에 에이전트 68개 · 스킬 292개 · 명령어 94개를 추가해주는 오픈소스 플러그인입니다. 만든 사람은 앤트로픽 × Forum Ventures 해커톤(2025년 9월) 우승자 Affaan Mustafa입니다. MIT 라이선스, 무료.
쉽게 말하면 클로드 혼자 코딩하던 걸 기획 → 테스트 → 구현 → 코드리뷰 → 검증처럼 역할과 절차를 나눠서 진행하도록 돕는 세트예요. 개발용이 메인이지만 글쓰기·SNS 콘텐츠·시장조사 같은 비개발 스킬도 들어 있습니다.

아래 페이지에서 상세가이드확인가능합니다.
[https://grand-count-2aa.notion.site/AI-3e550157ef1380e98bd0e935f5289475](https://grand-count-2aa.notion.site/AI-3e550157ef1380e98bd0e935f5289475)



설치 전 준비 Node.js 18 이상 · Git · Claude Code 2.1 이상
설치 — 둘 중 하나만
방법 A: 🟠 클로드코드 안에서 한 줄씩
```
/plugin marketplace add https://github.com/affaan-m/ECC
```
```
/plugin install ecc@ecc
```
방법 B: ⚫ 터미널에서 한 줄
```
npx ecc-universal@2.2.2 setup
```
물어보는 항목: 범위 → Global user / 훅 프로필 → Standard / 확인 → Yes
⚠️ A와 B는 같은 플러그인을 설치하는 두 가지 방법입니다. 섞지 마세요.
설치 확인 클로드코드를 새로 켜고 `/plugin list` → `ecc@ecc`가 설치·활성화되어 있는지 확인. `/ecc:`를 입력해 명령어·스킬 목록도 확인.
뭐부터 써야 하나요 (전부 다 쓰려고 하지 마세요)
개발하는 분:
개발 안 하는 분 (채팅에 스킬 이름 + 원하는 작업):
주의 설치된 스킬의 목록·설명은 컨텍스트를 차지합니다. 많이 깔수록 무거워질 수 있어요. 내 작업에 맞는 것만 물어보기:
```
npx ecc-universal@2.2.2 consult "article writing" --target claude
```
참고 깃허브: github.com/affaan-m/ECC · 한국어 README 있음
상세 가이드: https://grand-count-2aa.notion.site/AI-3e550157ef1380e98bd0e935f5289475

---

#### 10-2. skill-creator (공식) — 내 스킬 직접 만들기
뭘 해주나요?
위 도구들은 남이 만든 스킬입니다. 이건 내 스킬을 만드는 스킬입니다. 내가 일하는 방식을 대화로 설명하면 클로드가 그걸 스킬 폴더로 만들어줍니다. 그다음부터는 매번 설명할 필요 없이 주제만 던지면 그 방식대로 나옵니다.
클로드 웹(앱)에서 — 설치 없음. 사용자 지정(Customize) → 스킬(Skills) 에서 skill-creator 켜고 "스킬 하나 만들고 싶어"라고 시작.
클로드코드에서 🟠 공식 스킬이라 한 줄입니다.
```
/plugin install skill-creator@claude-plugins-official
```
상세 화면이 뜨면 맨 위 Install for you (user scope) 를 고르고 Enter. 끝나면 `/reload-plugins`.
이미 설치되어 있는 분은 `Installed` 탭에 skill-creator가 보이거나, 설치 선택지 대신 업데이트/제거 메뉴가 뜹니다. 그 경우엔 설치를 건너뛰세요.
바로 써보기
```
스킬 하나 만들고 싶어. 주간 업무 보고서 쓰는 스킬인데,
구조는 ① 이번 주 완료 ② 진행 중 ③ 다음 주 계획 ④ 이슈 순서고,
각 항목은 3줄 이내, 숫자가 있으면 반드시 넣고, 존댓말로.
```
만들어지면 (예: 만들어진 명칭이 weekly-report):
```
/weekly-report 이번 주 한 일: 로그인 버그 수정, 신규 기능 기획 회의 2번, 서버 이전 준비 시작
```
활용 예시
팁 규칙은 구체적일수록 좋습니다. 만든 스킬은 `.claude\skills\` 안에 폴더로 생깁니다. 팀원한테 폴더째 보내면 그 사람도 바로 씁니다.

---

#### 10-3. claude-mem — 대화 넘어 기억
뭘 해주나요?
클로드코드는 대화를 끄면 다 잊습니다. 이걸 깔면 세션에서 한 일을 자동으로 압축해 저장해 두고, 다음에 켤 때 관련 내용을 알아서 불러옵니다. "어제 어디까지 했지?"를 설명할 필요가 없어집니다. 저장은 전부 내 컴퓨터 안에.
필요한 것 Node.js 18 이상 · Bun·uv (없으면 설치 중에 자동으로 깔아줌)
설치 🟠 클로드코드에 아래 문장을 그대로 붙여넣으세요.
txt
```
npx claude-mem install 실행해줘. 어느 도구에 설치할지 물으면 Claude Code, bun이나 uv 설치하겠냐고 물으면 yes.
```
이 한 줄이 플러그인 등록 + 훅 설정 + 저장 프로그램(워커) 시작까지 다 합니다.
> ⚠️ `/plugin install claude-mem`으로 깔면 플러그인만 등록되고 워커가 안 켜지는 경우가 있습니다 (윈도우에서 특히). 그렇게 깔았다면 위 `npx claude-mem install`을 한 번 더 돌리면 됩니다.
⚠️ `/plugin` 목록에 `claude-mem-cowork`도 보이는데, 그건 클라우드 동기화용 다른 플러그인입니다. 깔지 마세요. 깔았다면 `/plugin uninstall claude-mem-cowork@thedotmack`.
⚠️ `npm install -g claude-mem`은 라이브러리만 깔립니다. 위 `npx claude-mem install`이어야 합니다.
설치 후 반드시 VS Code를 완전히 종료하고 다시 여세요. 터미널만 새로 여는 걸로는 부족합니다. 안 그러면 “Executable not found in $PATH: claude-mem” 훅 에러가 뜹니다 — 설치가 실행 파일을 새 위치에 넣었는데 켜져 있던 클로드코드가 그 경로를 모르기 때문입니다.
설치 확인 🟠 클로드코드에서
txt
```
npx claude-mem status 실행해줘
```
“worker running”과 포트 37777이 나오면 성공. 저장된 기억은 브라우저에서 [http://localhost:37777](http://localhost:37777/) 로 볼 수 있습니다 (클로드코드가 켜져 있을 때만).
안 되면:
txt
```
npx claude-mem doctor 실행해줘
```
→ bun·uv·훅·워커 중 뭐가 빠졌는지 알려줍니다.
바로 써보기 설치 후엔 따로 부를 게 없습니다. 깐 직후엔 저장된 게 없어서 아무것도 안 뜨는 게 정상입니다. 작업 하나 하고 클로드코드를 껐다 켜면 상단에 이전 세션 요약이 뜹니다. 그다음부터:
txt
```
어제 이 프로젝트에서 뭐 하다 끝났어? 이어서 하자.
```
주의
- 모든 세션을 기록하니 대화에 넣은 민감한 정보도 저장됩니다. 저장 위치는 `~/.claude-mem/` (윈도우 `C:\Users\내이름\.claude-mem\`).
- 설정은 `~/.claude-mem/settings.json`. 한국어 모드는 아직 없고 영어·중국어·일본어 모드가 있습니다.
참고 깃허브: github.com/thedotmack/claude-mem


---

#### 10-4. nuwa-skill — 유명인 사고방식을 스킬로
뭘 해주나요?
유명인이 공개적으로 남긴 글·인터뷰·결정 기록을 분석해서, 그 사람의 생각하는 방식을 스킬로 만들어 둔 모음입니다. 한 번 설치하면 머스크, 스티브 잡스, 찰리 멍거 등 14명이 한꺼번에 들어옵니다. 쓸 때는 "머스크 관점으로 봐줘"처럼 이름만 말하면 됩니다.
설치 🟠 클로드코드를 켜고 아래 문장을 그대로 붙여넣으세요.
```
아래 명령을 그대로 실행해서 nuwa-skill과 그 안의 인물 스킬을 전부 설치해줘.

npx -y skills@latest add alchaincyf/nuwa-skill --full-depth --skill '*' -a claude-code -g -y

설치가 끝나면 설치된 스킬 이름 목록을 보여줘.
```
목록에 `elon-musk-perspective` 등 16개가 보이면 성공입니다. 그다음 `/reload-plugins`
들어 있는 인물
바로 써보기 끝에 "한국어로 답해줘"를 꼭 붙이세요. 스킬이 중국어로 만들어져 있어서, 안 붙이면 중국어로 답할 수 있습니다.
```
머스크 관점으로 내 사업 아이디어를 피드백해줘. 한국어로 답해줘.

아이디어: 직장인 대상 AI 활용 온라인 강의. 월 구독 2만 원. 주 1회 라이브.
```
주의
- 실제 그 사람의 의견이 아닙니다. 공개된 자료로 정리한 "생각하는 틀"일 뿐입니다.
- 투자·사업 결정은 참고용으로만 쓰고, 최종 판단은 본인이 하세요.
참고 깃허브: github.com/alchaincyf/nuwa-skill · 한국어 README 있음, 무료 (MIT)

---

#### 10-5. superpowers — 클로드 일하는 방식 통째로 교체
뭘 해주나요? 클로드코드가 바로 코드부터 쓰지 않고, 먼저 뭘 만들려는지 확인한 뒤 계획 → 테스트 → 실행 순서로 진행하게 만드는 스킬. 시니어 개발자의 일하는 방식을 심어줍니다. `/brainstorm`, `/write-plan`, `/execute-plan` 명령이 생겨요. 개발이 아니라도 "뭔가 만들기 전에 같이 생각 정리"에 좋습니다.
설치 방법 A — 플러그인 🟠 클로드코드 안에서 한 줄씩
```
/plugin marketplace add obra/superpowers-marketplace
```
```
/plugin install superpowers@superpowers-marketplace
```
설치 방법 B — 스킬 한 줄 ⚫ 일반 터미널
```
npx skills add obra/superpowers -g
```
설치 후 클로드코드를 재시작하세요.
> 설치법이 바뀔 수 있어요. 안 되면 github.com/obra/superpowers 확인!
바로 써보기
```
/brainstorm 내 강의 신청 받는 랜딩페이지 만들고 싶어. 구조부터 같이 잡자.
```
스킬 넣기 전엔 클로드가 바로 코드부터 씁니다. 넣은 후엔 어떤 방식으로 만들지 먼저 확인하거나, 계획을 세운 뒤 진행하는 걸 확인할 수 있습니다.
참고 깃허브: github.com/obra/superpowers

---





*삭제 하는법
- 플러그인: `/plugin` → Installed → 해당 항목 → Uninstall
- 폴더로 넣은 스킬: `~/.claude/skills/` 안의 해당 폴더 삭제
- MCP: ⚫ `claude mcp remove 이름`
- Firecrawl 연결 해제: ⚫ `firecrawl logout`
- 커넥터: 클로드 앱 커넥터 화면에서 연결 해제

---

### 자주 막히는 곳
`/plugin` 을 쳤는데 아무 반응이 없어요
먼저 일반 터미널이 아니라 클로드코드 입력창에 넣었는지 확인하세요. 맞게 넣었는데도 안 되면 일반 터미널에서 `claude update` 를 실행하고 다시 해보세요.
`/plugin install` 에서 "Marketplace not found"라고 떠요
순서 문제입니다. `marketplace add` 먼저 → 완료 확인 → `install`. 꼭 한 줄씩!
"not a valid GitHub owner/repo" 에러가 나요
두 줄을 한꺼번에 붙여넣어서 그렇습니다. 한 줄 입력하고 엔터, 완료되면 다음 줄.
설치 범위(scope)를 고르라고 나와요User(사용자) 를 고르세요. 같은 컴퓨터의 어느 폴더에서든 쓸 수 있는 전역 설치입니다.
설치했는데 명령어가 안 보여요
설치 완료 안내를 따랐는지 확인하고, 클로드코드를 완전히 껐다가 다시 켜세요. 폴더로 넣은 스킬은 `/reload-plugins`.
`/reload-plugins` 쳐도 스킬이 안 떠요
폴더 안에 `SKILL.md`가 있는지 확인하세요. 경로가 `.claude\skills\<스킬이름>\SKILL.md` 형태여야 합니다.
`npx skills`가 어느 도구에 설치할지 물어봐요
Claude Code를 고르세요.
`npx` 나 `npm` 을 찾을 수 없다고 나와요
Node.js가 없거나, 설치 후 VS Code를 다시 안 켠 경우입니다. 준비물 항목을 다시 따라 하세요.
MCP가 `/mcp` 목록에 안 떠요
클로드코드를 완전히 껐다가 다시 켜보세요. 키 에러가 나면 명령어의 `발급받은키` 자리에 진짜 키를 넣었는지 확인 후 다시 실행.
스킬이 자동으로 안 쓰여요
이름을 직접 부르세요: "○○ 스킬 써서 ~해줘" 또는 `/스킬이름`. 그래도 안 되면 스킬이 너무 많은지 확인하세요.
클로드가 자꾸 엉뚱한 스킬을 쓰거나 멍청해진 것 같아요`/skills`에서 총 개수를 보세요. 수십 개가 넘으면 스킬 설명이 잘려서 클로드가 매칭을 못 하기 시작합니다. 안 쓰는 스킬 폴더를 지우는 게 최고의 성능 튜닝입니다. 50개를 다 깔라는 가이드가 아닙니다.
Agency Agents 담당자를 불렀는데 평소랑 똑같이 답해요
설치된 에이전트의 정확한 이름을 확인한 뒤, "(에이전트 이름) 에이전트를 서브에이전트로 호출해서 작업해줘"라고 요청하세요. 화면에 그 에이전트를 호출했다는 표시가 뜨는지 보세요.
머스크 스킬이 중국어로 답해요
질문 끝에 "한국어로 답해줘"를 붙이세요. 매번 귀찮으면 "앞으로 이 스킬은 항상 한국어로 답해줘"라고 한 번 말해두면 됩니다.
hyperframes 렌더에서 ffmpeg 오류가 나요
FFmpeg가 없는 겁니다. 설치하고 VS Code를 껐다 켜세요. `npx hyperframes doctor`를 치면 빠진 걸 알려줍니다.
자동 모드를 켰는데 다음에 켜니 꺼져 있어요
정상입니다. 터미널을 새로 열 때마다 Shift + Tab을 다시 눌러야 합니다.
Composio 커스텀 커넥터 추가 메뉴가 없어요
무료 플랜일 가능성이 높아요. 유료 플랜에서 지원되는 기능입니다.
뭔가 꼬였는데 원인을 모르겠어요
에러 메시지를 그대로 클로드코드에 붙여넣고 "이거 왜 이런 거야? 고쳐줘" 하세요. API 키 같은 민감정보는 가리고.

---

이 가이드는 2026년 10월 기준으로 정리했습니다. 도구는 자주 업데이트되니 설치가 안 되면 각 깃허브 README를 확인하세요.
@mini_ai_ 팔로우하고 다음 꿀팁도 받아가세요.

---

