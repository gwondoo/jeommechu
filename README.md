# 점메추 Discord Bot

회사 주변 전체 식당 또는 직접 등록한 식권대장 목록에서 점심을 추천하는 Discord 봇입니다.

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
docker compose restart
```

`bot.py`, `kakao.py`, `storage.py`는 호스트 파일을 컨테이너에 직접 연결합니다. 따라서 위 명령어만으로 코드 변경이 반영됩니다. `requirements.txt` 또는 `Dockerfile`을 변경했을 때만 이미지를 다시 빌드하세요.

```bash
docker compose up -d --build
```

종료:

```bash
docker compose down
```

`data/`는 호스트 볼륨으로 연결됩니다. SQLite 파일 `data/jeommechu.db`는 컨테이너 밖에 있으므로 `docker compose down`, 재시작, 이미지 재빌드 뒤에도 회사 설정, 식권대장, 주변 검색 결과와 추천 이력이 유지됩니다. `docker compose down -v`를 실행하거나 호스트의 `data/`를 직접 삭제하지 않는 한 데이터는 사라지지 않습니다.

처음 SQLite 버전으로 실행할 때 기존 `data/state.json`이 있으면 자동으로 데이터를 이전합니다. 이전 파일은 안전을 위해 삭제하지 않습니다.

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

- `/점메추 [종류] [거리]`: 회사 주변 전체 식당에서 추천
- `/식권대장 [종류]`: 식권대장에 등록한 식당에서 추천
- `/식권관리 목록|추가|수정|삭제`: 식권대장 관리
- `/회사주소설정 주소 [반경]`: 회사 위치 설정
- `/회사주소조회`: 회사 위치 조회
- `/도움말`: 명령어 안내

데이터는 `data/jeommechu.db`에 저장됩니다. 회사 주소나 기본 반경이 변경되면 해당 Discord 서버의 주변 검색 결과와 주변 추천 이력만 초기화되며, 직접 등록한 식권대장 데이터는 유지됩니다.

종류를 지정하지 않은 주변 추천은 반경 안에서 한식, 중식, 일식, 양식, 아시아음식, 분식, 치킨, 피자와 기타 음식점을 각각 최대 45곳 조회합니다. 그룹별 결과를 SQLite에 따로 저장하고, 추천할 때 합쳐 카카오 장소 ID로 중복을 제거합니다. 모든 검색에 카카오 음식점 그룹(`FD6`)을 강제하므로 별도 카페 그룹(`CE7`)은 제외됩니다. 각 그룹 결과는 6시간 동안 재사용합니다.
