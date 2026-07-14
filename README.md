# 점메추 Discord Bot

식권대장 목록에서 점심을 추천하고, 필요하면 회사 주변 식당도 추천하는 Discord 봇입니다.

## 준비

1. Python 3.11 이상을 설치합니다.
2. Discord Developer Portal에서 Application을 만든 뒤 Bot Token을 발급합니다.
3. OAuth2 URL Generator에서 `bot`, `applications.commands` 범위를 선택해 봇을 서버에 초대합니다.
4. Kakao Developers에서 앱을 만들고 REST API 키를 발급합니다.
5. `.env.example`을 `.env`로 복사해 값을 채웁니다.

Docker Compose로 실행하는 것을 권장합니다.

```bash
cp .env.example .env
# .env에 키를 입력
docker compose up -d --build
```

상태 확인과 로그 확인:

```bash
docker compose ps
docker compose logs -f
```

코드를 수정한 뒤 다시 배포:

```bash
docker compose up -d --build
```

종료:

```bash
docker compose down
```

`data/`는 호스트 볼륨으로 연결됩니다. 따라서 `docker compose down` 또는 이미지 재빌드 뒤에도 `data/state.json`의 회사 설정과 식권대장 목록은 유지됩니다.

Docker 없이 실행하려면 다음을 사용합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

개발 중에는 `.env`의 `DISCORD_GUILD_ID`를 설정하세요. 해당 서버에만 명령어가 즉시 동기화됩니다.
비워두면 전역 명령어로 동기화되며 Discord에 반영되기까지 시간이 걸릴 수 있습니다.

## 명령어

- `/점메추 [종류]`: 식권대장 식당에서 추천
- `/식권대장싫어 [종류] [거리]`: 회사 주변 모든 식당에서 추천
- `/식권대장 목록|추가|수정|삭제`: 식권대장 관리
- `/회사주소설정 주소 [반경]`: 회사 위치 설정
- `/회사주소조회`: 회사 위치 조회
- `/도움말`: 명령어 안내

식권대장 데이터는 `data/state.json`에 저장됩니다. 이 파일에는 API 키를 넣지 마세요.
