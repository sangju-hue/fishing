# 빈자리 알림 신청 중계

GitHub Pages는 RSA/AES로 암호화한 신청만 Worker/D1에 저장합니다. Mac이 HTTPS 외부 요청으로 신청을 가져와 이름 허용 목록과 소유자 권한을 검사하고 처리합니다. Mac에 외부 접속 포트를 열지 않습니다. ntfy는 빈자리 폰 알림 전송에만 사용합니다.

## 배포

1. 이 폴더에서 `npm ci`, `npx wrangler login`으로 사용자 Cloudflare 계정을 인증합니다.
2. `npx wrangler d1 create fishing-alert-relay` 결과의 database_id를 wrangler.json에 넣습니다.
3. `npx wrangler d1 execute fishing-alert-relay --remote --file=schema.sql`을 실행합니다.
4. 무작위 64자리 hex AGENT_TOKEN을 Worker secret으로 저장하고 같은 값을 Mac `.ntfy/relay.json`에 기록합니다. 파일 권한은 600입니다. 토큰을 GitHub에 올리지 않습니다.
5. `npx wrangler deploy`로 배포한 workers.dev 주소를 `.ntfy/relay.json`의 url로 지정합니다.
6. /health 및 인증된 /agent/requests 응답을 확인한 뒤 Mac 수집기를 재시작합니다. 실행 중인 수집이 있으면 완료 후 재시작합니다.
7. 생성된 data/ntfy_public.json의 relay_url과 프론트 코드를 함께 게시합니다. relay_url 활성화 전에는 기존 신청 경로가 유지됩니다.

설정 형태: `{ "url": "https://배포주소.workers.dev", "agent_token": "64자리 비밀값" }`.

Mac은 30초마다 신청을 확인하며 연결 장애 시 최대 5분까지 재시도 간격을 늘립니다. 웹은 신청 중에만 최대 90초간 결과를 확인합니다. Mac이 꺼져 있으면 24시간 동안 신청이 대기합니다. 처리된 신청의 암호문은 삭제하고 결과는 48시간 뒤 삭제합니다. 같은 신청 식별값의 재전송은 다시 등록하지 않습니다. 기존 알림 설정은 보존합니다.

IP당 시간당 60건, 전체 대기 최대 300건으로 제한합니다. Origin 검사는 사용자 인증을 대신하지 않으며, 실제 이름 제한과 수정 권한은 Mac이 검증합니다. 실명 입력 자체가 신원 인증은 아닙니다.

검증: `node test.mjs`, 프로젝트 루트에서 `python3 -m unittest tests.test_alert_relay tests.test_ntfy_alerts`, `node tests/test_alert_relay.cjs`, `node tests/test_ntfy_receipts.cjs`.
