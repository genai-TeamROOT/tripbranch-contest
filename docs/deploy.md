# 배포 운영 문서

공모전 제출용 배포의 구축 절차와 일상 운영을 적는다. 본 저장소
(`genai-TeamROOT/tripbranch`)의 배포와 **인프라를 공유하지 않는다** — 심사 기간에
개발 중인 쪽의 배포가 이쪽을 흔들면 안 되기 때문이다.

```
브라우저
 ├ contest.tripbranch.co.kr      -> CloudFront -> S3 (프론트 정적 파일)
 └ api-contest.tripbranch.co.kr  -> EIP -> EC2 t3.micro
                                           └ Caddy :443 (Let's Encrypt)
                                               └ 127.0.0.1:8000 컨테이너
```

- AWS 계정 `577101745292`, 리전 `ap-northeast-2`(CloudFront 인증서만 `us-east-1`).
- 도메인은 가비아 등록 + **Cloudflare DNS**. 레코드는 전부 프록시를 끄고 DNS only.
- 배포는 GitHub Actions에서 OIDC로 역할을 맡아 진행한다. **액세스 키를 쓰지 않는다.**
- EC2에는 **SSH 포트를 열지 않는다.** 접속은 SSM Session Manager로만 한다.

## 브랜치와 배포 시점

```
작업 브랜치  ->  develop  ->  (검수)  ->  main  ->  배포
```

- **`main`에 들어온 것만 배포된다.** 배포 워크플로우는 `main` 푸시에만 반응한다.
- `develop`은 통합 브랜치다. 여기에 머지해도 배포되지 않으므로, 검수는 로컬이나
  PR 단위로 한다.
- 워크플로우는 경로 필터를 쓴다. `backend/**`가 바뀌면 백엔드만, `frontend/**`가
  바뀌면 프론트만 돈다. 둘 다 바뀌면 둘 다 돈다.
- 급하게 되돌려야 하면 `main`을 고치기 전에 **롤백 절차**(아래)를 먼저 쓴다.
  이미 ECR에 있는 이전 이미지를 띄우는 쪽이 새 배포보다 빠르다.

## 리소스 목록

| 종류 | 이름 / ID | 비고 |
| --- | --- | --- |
| ECR | `tripbranch-contest-backend` | 수명주기: 최근 5개 유지 |
| IAM 역할 | `github-actions-deploy-contest` | OIDC 공급자는 계정에 이미 있는 것을 재사용 |
| 보안 그룹 | `tripbranch-contest-sg` (`sg-0f8b29b42cac41004`) | 인바운드 80/443만. 인스턴스의 sshd도 mask 상태(7-5) |
| EC2 | `i-017735bd69e603ad5` | t3.micro, AL2023 x86_64, 30GB gp3 |
| EIP | `43.203.3.121` | |
| S3 | `tripbranch-contest-frontend` | 퍼블릭 액세스 차단 유지 |
| CloudFront | `E1LJB7J6DXZ78S` | |
| ACM | `arn:aws:acm:us-east-1:577101745292:certificate/c53345f4-d639-496d-b169-b0676e1d9463` | **us-east-1** |
| EC2 인스턴스 프로파일 | `tripbranch-ec2-role` | 원본 프로젝트와 공유(SSM Core + ECR ReadOnly 읽기 전용) |
| CloudWatch 경보 | `tripbranch-contest-surplus-credits` | 초과 크레딧 > 5 -> 인스턴스 중지 |
| CloudWatch 경보 | `tripbranch-contest-instance-status` | 상태 검사 2분 실패 -> 재부팅 |
| SNS 주제 | `tripbranch-contest-alerts` | 두 경보의 이메일 알림 |
| EBS 스냅샷 | `snap-096939c6b5ad7d7e7` | 2026-09-19 복구 지점 |

2026-09-19 구축 완료. 위 값은 실제로 만들어진 리소스다.
2026-09-20에 접근 로그를 켜고 sshd를 내렸다.

---

# 최초 구축

## 1. ECR 리포지토리

`tripbranch-contest-backend` 생성. 수명주기 규칙으로 **최근 5개만 유지**를 걸어둔다
(배포마다 커밋 SHA 태그가 쌓인다).

## 2. IAM 역할

`github-actions-deploy-contest`를 만들고 아래 두 정책을 넣는다.

### 신뢰 정책

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::577101745292:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:genai-TeamROOT/tripbranch-contest:*"
        }
      }
    }
  ]
}
```

`sub` 조건이 이 저장소로 못박혀 있어야 한다. 빼면 **다른 저장소의 워크플로우도**
이 역할을 맡을 수 있다.

### 권한 정책

`i-017735bd69e603ad5`, `tripbranch-contest-frontend`, `E1LJB7J6DXZ78S`를 채운 뒤 인라인 정책으로 붙인다.
리소스를 좁혀두는 이유는, 이 역할이 탈취되거나 워크플로우가 잘못 수정돼도
**본 프로젝트 인스턴스와 ECR을 건드릴 수 없게** 하기 위해서다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPushPull",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:DescribeImages"
      ],
      "Resource": "arn:aws:ecr:ap-northeast-2:577101745292:repository/tripbranch-contest-backend"
    },
    {
      "Sid": "SsmSendCommand",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:577101745292:instance/i-017735bd69e603ad5",
        "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "SsmReadResult",
      "Effect": "Allow",
      "Action": [
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3FrontendBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::tripbranch-contest-frontend"
    },
    {
      "Sid": "S3FrontendObjects",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::tripbranch-contest-frontend/*"
    },
    {
      "Sid": "CloudFrontInvalidate",
      "Effect": "Allow",
      "Action": "cloudfront:CreateInvalidation",
      "Resource": "arn:aws:cloudfront::577101745292:distribution/E1LJB7J6DXZ78S"
    }
  ]
}
```

S3·CloudFront 부분은 9단계에서 리소스를 만든 뒤에 채워도 된다. 백엔드 배포만
먼저 돌리려면 ECR·SSM 구문만 있어도 동작한다.

### 신뢰 정책의 `sub`에 주의 — 실제로 여기서 막혔다

위 신뢰 정책은 **이 저장소 기준**이다. 원본 저장소(`genai-TeamROOT/tripbranch`)의
값을 그대로 가져오면 실패한다.

```
원본 저장소  : repo:genai-TeamROOT/tripbranch:*
이 저장소    : repo:genai-TeamROOT@304239192/tripbranch-contest@1375480282:ref:refs/heads/main
```

차이는 GitHub의 **immutable subject claims**다. 켜져 있으면 `sub`의 조직·저장소
이름 뒤에 숫자 ID가 붙는다. 저장소를 지웠다 같은 이름으로 다시 만들어도 다른
주체로 취급하게 하는 보안 기능이라 끄지 않는다.

**원본 저장소는 이 기능이 꺼져 있고 이 저장소는 켜져 있다.** 그래서 검증된 설정을
그대로 옮겼는데도 2026-09-19 첫 배포가 `Not authorized to perform
sts:AssumeRoleWithWebIdentity`로 실패했다. 와일드카드(`:*`)를 써도 마찬가지다 —
`genai-TeamROOT` 다음이 `/`가 아니라 `@304239192`라서 패턴 자체가 어긋난다.

자기 저장소의 실제 값은 이렇게 확인한다.

```bash
gh api /repos/genai-TeamROOT/tripbranch-contest/actions/oidc/customization/sub
```

`sub_claim_prefix`가 나오고, 거기에 `:ref:refs/heads/main`을 붙인 것이 최종 `sub`다.

## 3. 보안 그룹

`tripbranch-contest-sg`, VPC는 기본 VPC(`vpc-0185215c2cb04ae38`).

| 방향 | 포트 | 소스 |
| --- | --- | --- |
| 인바운드 | 80 (TCP) | `0.0.0.0/0` |
| 인바운드 | 443 (TCP) | `0.0.0.0/0` |

**22번(SSH)은 열지 않는다.** 접속은 SSM으로 한다. 80번은 Let's Encrypt의
HTTP-01 챌린지와 HTTPS 리디렉트에 쓰인다.

**인스턴스 안의 sshd도 끈다**(7-5). 보안 그룹이 막고 있어도 데몬이 떠 있으면
방어선이 그룹 규칙 하나뿐이다 — 22번을 실수로 여는 순간 바로 노출된다.

## 4. EC2 인스턴스

| 항목 | 값 |
| --- | --- |
| AMI | Amazon Linux 2023, x86_64 |
| 인스턴스 유형 | `t3.micro` |
| 키 페어 | **없음** (SSH를 안 쓰므로) |
| 보안 그룹 | `tripbranch-contest-sg` |
| 스토리지 | 30GB gp3 |
| IAM 인스턴스 프로파일 | `tripbranch-ec2-role` |
| 크레딧 사양 | `unlimited` (기본값 그대로) |

인스턴스 프로파일을 빼먹으면 SSM 접속도, ECR pull도 안 된다. 시작 후
Systems Manager -> 플릿 관리자에 인스턴스가 올라오는지로 확인한다(1~2분 걸린다).

## 5. EIP 할당·연결

EC2 -> 네트워크 및 보안 -> 탄력적 IP -> 할당 -> 작업 -> 탄력적 IP 주소 연결.

인스턴스를 만들기 **전에** 할당해두면 연결 안 된 EIP로 요금이 붙는다. 만든 직후에
할당하고 바로 연결한다.

## 6. Cloudflare A 레코드

DNS -> 레코드 -> 레코드 추가.

| 형식 | 이름 | 값 | 프록시 |
| --- | --- | --- | --- |
| A | `api-contest` | `43.203.3.121` | **DNS only (회색 구름)** |

이름 칸에는 서브도메인만 넣는다. 프록시를 켜면 (1) Let's Encrypt 발급이 막히고
(2) SSL 모드가 Flexible이면 Caddy와 리디렉트 루프가 나고 (3) 무료 플랜의 100초
제한에 SSE 스트리밍이 끊긴다.

확인:

```
dig +short api-contest.tripbranch.co.kr
```

EIP가 그대로 나와야 한다. `104.x.x.x`나 `172.67.x.x`가 나오면 프록시가 켜진 것이다.

## 7. EC2 부트스트랩

Systems Manager -> Session Manager -> 세션 시작에서 인스턴스를 고른다.

### 7-1. Docker

```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo docker version
```

### 7-2. 스왑 1GB

t3.micro는 메모리가 1GB다. 실사용은 200MB 안팎으로 추정하지만, 순간적으로 튈 때
OOM으로 컨테이너가 죽는 대신 느려지기만 하도록 스왑을 깔아둔다.

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

### 7-3. 환경 변수 파일

```bash
sudo mkdir -p /opt/tripbranch
sudo install -m 600 /dev/null /opt/tripbranch/.env
sudo vi /opt/tripbranch/.env
```

`backend/.env.example`을 보고 값을 채운다. **이 파일은 저장소에 올리지 않는다.**
공모전 배포에서 반드시 확인할 항목은 다음과 같다.

| 변수 | 값 | 이유 |
| --- | --- | --- |
| `APP_ENV` | `production` | |
| `PROVIDER_MODE` | `real` | |
| `CORS_ALLOW_ORIGINS` | `https://contest.tripbranch.co.kr` | 프론트가 다른 오리진이다 |
| `TASTE_EVIDENCE_ENABLED` | `false` | 이미지에 모델이 없다. 켜면 첫 요청에서 ImportError |
| `PLACE_MOOD_ENABLED` | `false` | 같은 이유 |
| `PLACE_MOOD_WARMUP_ENABLED` | `false` | |
| `PLACE_MOOD_RERANK_ENABLED` | `false` | |
| `RATE_LIMIT_ENABLED` | `true` | `/api/chat`이 무인증이라 공개 배포에서는 켠다 |

권한이 600인지 확인한다.

```bash
ls -l /opt/tripbranch/.env
```

### 7-4. Caddy

Caddy도 컨테이너로 띄운다. AL2023에 패키지로 넣으려면 COPR 저장소를 붙여야 하는데,
Docker가 이미 있으니 그럴 이유가 없다.

```bash
sudo mkdir -p /opt/caddy
sudo tee /opt/caddy/Caddyfile >/dev/null <<'EOF'
{
	email <운영자 이메일>
}

api-contest.tripbranch.co.kr {
	# 접근 로그. 이게 없으면 누가 무엇을 찔렀는지 남는 곳이 한 군데도 없다
	# (2026-09-20에 켰다). **stdout 이 아니라 파일로 뺀다** — 도커 json-file
	# 드라이버에 회전 설정이 없어서 stdout 으로 두면 디스크가 무한히 찬다.
	# /data 는 caddy_data 볼륨이라 컨테이너를 다시 띄워도 로그가 남는다.
	log {
		output file /data/access.log {
			roll_size 10MiB
			roll_keep 5
			roll_keep_for 720h
		}
		format json
	}

	# 보안 헤더. CSP는 default-src none으로 둘 수 있지만, 이 응답은 JSON만
	# 내보내고 브라우저가 문서로 렌더링할 일이 없어 실익이 적다. 대신 문서
	# 경로를 아래에서 아예 막는다.
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "strict-origin-when-cross-origin"
		# uvicorn임을 굳이 알릴 이유가 없다.
		-Server
	}

	# 엣지에서 떨군다. FastAPI는 /docs·/redoc·/openapi.json을 기본으로 열어두는데,
	# 공개 인터넷에 그대로 두면 API 스키마가 그대로 읽힌다. /api/chat이 무인증이라
	# 스키마를 아는 순간 자동화된 호출로 LLM 비용을 태울 수 있다.
	#
	# 통계 두 개는 내부 운영 지표(피드백 분포, 단계별 지연·에러 수)를 무인증으로
	# 내보낸다. 개발자 Ops 화면이 쓰던 것이고 공개 배포에서는 쓸 일이 없다.
	@blocked path /docs* /redoc* /openapi.json /api/feedback/stats /api/trace/stats
	respond @blocked 404

	reverse_proxy 127.0.0.1:8000
}
EOF

sudo docker run -d --name caddy --restart unless-stopped \
  --network host \
  -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v caddy_data:/data \
  -v caddy_config:/config \
  caddy:2
```

- `--network host`를 쓰는 이유는 두 가지다. 80/443을 그대로 잡아야 하고, 백엔드
  컨테이너가 `127.0.0.1:8000`에만 열려 있어서 호스트 네트워크에서만 닿는다.
- `caddy_data` 볼륨에 발급받은 인증서가 저장된다. **이 볼륨을 지우면 인증서를
  다시 발급받는다** — Let's Encrypt에는 발급 횟수 제한이 있으니 함부로 지우지 않는다.
- SSE는 따로 설정할 게 없다. Caddy는 `text/event-stream` 응답을 감지하면 버퍼링을
  끄고 그대로 흘려보낸다.

인증서 발급 확인:

```bash
sudo docker logs caddy | tail -20
```

`certificate obtained successfully` 같은 줄이 보이면 된다. 아직 백엔드 컨테이너가
없으므로 접속하면 502가 나는 게 정상이다.

`caddy_data` 볼륨에는 인증서 말고 접근 로그(`/data/access.log`)도 들어간다.

### 7-5. sshd 끄기

AL2023은 sshd가 켜진 채로 뜬다. 보안 그룹이 22번을 막고 있어 외부에서 닿지는
않지만, **방어선이 그룹 규칙 하나뿐인 상태**다. 접속은 SSM으로만 하기로 했으니
데몬 자체를 내린다.

```bash
sudo systemctl stop sshd
sudo systemctl disable sshd
sudo systemctl mask sshd
```

`mask`까지 거는 이유는 `disable`만으로는 다른 유닛이 의존성으로 끌어올릴 수
있어서다. 마스크하면 `/etc/systemd/system/sshd.service`가 `/dev/null`로 연결돼
어떤 경로로도 올라오지 않는다.

확인:

```bash
systemctl is-active sshd     # inactive
systemctl is-enabled sshd    # masked
ss -tln | grep -c ':22 '     # 0
systemctl is-active amazon-ssm-agent   # active — 이게 살아 있어야 접속이 된다
```

**끄기 전에 SSM 에이전트가 active인지 반드시 먼저 본다.** 둘 다 죽으면 인스턴스에
들어갈 길이 없어진다. 되돌리려면 `sudo systemctl unmask sshd && sudo systemctl
enable --now sshd`이지만, 그러려면 SSM으로 들어가야 하므로 순서가 중요하다.

## 8. GitHub 저장소 변수

Settings -> Secrets and variables -> Actions -> **Variables** 탭에 등록한다.
전부 Secrets가 아니라 Variables다 — `VITE_*`는 브라우저로 내려가는 값이라
애초에 비밀이 아니고, 나머지는 리소스 ID다.

| 이름 | 값 | 쓰는 워크플로우 |
| --- | --- | --- |
| `EC2_INSTANCE_ID` | `i-017735bd69e603ad5` | backend |
| `S3_BUCKET` | `tripbranch-contest-frontend` | frontend |
| `CLOUDFRONT_DISTRIBUTION_ID` | `E1LJB7J6DXZ78S` | frontend |
| `VITE_API_BASE_URL` | `https://api-contest.tripbranch.co.kr/api` | frontend |
| `VITE_SUPABASE_URL` | Supabase 프로젝트 URL | frontend |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable 키 | frontend |

`VITE_API_BASE_URL`의 **끝에 `/api`가 붙는다.** 프론트는 이 값에 `/chat` 같은
경로를 그대로 이어 붙인다(`frontend/src/api/client.ts`).

**서버 전용 키(`SUPABASE_SECRET_KEY`, `LLM_API_KEY` 등)는 GitHub에 올리지 않는다.**
그것들은 EC2의 `/opt/tripbranch/.env`에만 있다.

## 9. 백엔드 첫 배포

Actions -> Deploy backend -> Run workflow.

끝나면 확인한다.

```
curl -i https://api-contest.tripbranch.co.kr/api/health
```

## 10. ACM 인증서 (us-east-1)

**리전을 us-east-1로 바꾼 뒤** 인증서를 요청한다. CloudFront는 이 리전의 인증서만
쓴다.

- 도메인 이름: `contest.tripbranch.co.kr`
- 검증 방법: DNS 검증

발급되면 콘솔이 CNAME 이름과 값을 알려준다. Cloudflare에 등록한다.

| 형식 | 이름 | 값 | 프록시 |
| --- | --- | --- | --- |
| CNAME | `_xxxx.contest` | `_yyyy.acm-validations.aws.` | **DNS only** |

ACM이 보여주는 이름은 `_xxxx.contest.tripbranch.co.kr.`처럼 전체 형태다.
Cloudflare에는 **`_xxxx.contest`까지만** 넣는다. 통째로 붙여넣으면 존 이름이
두 번 붙는다. 자동 갱신을 위해 검증 후에도 레코드를 남겨둔다.

## 11. S3 + CloudFront

### S3

- 버킷 이름 `tripbranch-contest-frontend`, 리전 `ap-northeast-2`
- **퍼블릭 액세스 차단 유지** (CloudFront만 OAC로 읽는다)
- 정적 웹 사이트 호스팅은 **켜지 않는다** (OAC와 같이 쓰지 않는다)

### CloudFront

| 항목 | 값 |
| --- | --- |
| 원본 | 위 S3 버킷 |
| 원본 액세스 | **OAC** 생성 후 적용, 안내되는 버킷 정책을 복사해 적용 |
| 뷰어 프로토콜 | Redirect HTTP to HTTPS |
| 대체 도메인 이름 | `contest.tripbranch.co.kr` |
| 인증서 | 10단계에서 만든 것 |
| 기본 루트 객체 | `index.html` |

SPA라서 오류 응답 두 개를 반드시 설정한다. 없으면 새로고침할 때 XML 오류가 뜬다.

| HTTP 오류 코드 | 응답 페이지 경로 | 응답 코드 |
| --- | --- | --- |
| 403 | `/index.html` | 200 |
| 404 | `/index.html` | 200 |

### PWA 캐시 주의

프론트는 PWA라 빌드 산출물 최상위에 **이름이 고정인 파일**이 여럿 나온다.

```
index.html  sw.js  registerSW.js  manifest.webmanifest  favicon.svg  pwa-*.png
```

내용 해시가 붙는 것은 `assets/` 안쪽뿐이다. 그래서 배포 워크플로우는 `assets/`만
영구 캐시(`immutable`)로 올리고 나머지는 `no-cache, must-revalidate`로 올린다.
**서비스 워커를 영구 캐시에 넣으면 새 배포가 브라우저에 영영 닿지 않는다** —
`frontend/vite.config.ts`가 `registerType: "autoUpdate"`를 고른 이유를 CDN 층에서
되돌리는 셈이 된다.

CloudFront 쪽에 캐시 정책을 따로 만들 필요는 없다. 기본 정책이 오리진의
`Cache-Control`을 존중한다. 다만 **버킷에 이미 잘못된 헤더로 올라간 파일은
다시 올려야 고쳐진다** — 헤더는 객체에 저장되는 값이라 무효화만으로는 안 바뀐다.

### 응답 헤더 정책

프론트에도 같은 보안 헤더를 붙인다. CloudFront -> 정책 -> 응답 헤더에서 만들고
배포의 기본 캐시 동작에 연결한다.

| 정책 | `tripbranch-contest-security-headers` (`3896c563-641c-4bec-8c94-361a40a67590`) |
| --- | --- |
| Strict-Transport-Security | `max-age=31536000` (includeSubDomains·preload 없음) |
| X-Content-Type-Options | `nosniff` |
| X-Frame-Options | `DENY` |
| Referrer-Policy | `strict-origin-when-cross-origin` |
| 제거 헤더 | `Server` — 오리진이 S3라는 것을 가린다 |

**CSP는 넣지 않았다.** 넣으려면 `img-src`에 관광 API의 사진 도메인을 전부 열어야
하고(장소 사진이 외부에서 온다), 애니메이션 라이브러리가 인라인 스타일을 써서
`style-src 'unsafe-inline'`도 필요하다. 그 상태의 CSP는 얻는 것에 비해 깨질 위험이
크다. 넣는다면 Report-Only로 먼저 한 바퀴 돌려보고 정해야 한다.

`Server: CloudFront`는 남는다. CloudFront가 스스로 붙이는 값이라 제거 대상에
넣어도 지워지지 않는다. 가리려던 것은 오리진 종류였고 그건 해결됐다.

### Cloudflare CNAME

| 형식 | 이름 | 값 | 프록시 |
| --- | --- | --- | --- |
| CNAME | `contest` | `<CloudFront 배포 도메인>` | **DNS only** |

## 12. 프론트 첫 배포

Actions -> Deploy frontend -> Run workflow.

## 13. 경보 두 개

### 13-1. 크레딧 경보 (`tripbranch-contest-surplus-credits`)

CloudWatch -> 경보 -> 경보 생성.

**콘솔에서 만들어야 한다.** EC2 중지 작업이 붙은 경보는 서비스 연결 역할
`AWSServiceRoleForCloudWatchEvents`를 자동 생성하는데, 여기에 `iam:CreateServiceLinkedRole`이
필요하다. CLI로 만들려다 이 권한에서 막혔고, 콘솔은 같은 작업을 알아서 처리한다.
SNS 주제도 같은 화면에서 만들 수 있다.

| 항목 | 값 |
| --- | --- |
| 지표 | `EC2 -> 인스턴스별 지표 -> CPUSurplusCreditBalance` |
| 통계 / 기간 | 최대 / 5분 |
| 조건 | `> 5` |
| 누락된 데이터 처리 | **양호(정상)로 처리** |
| 작업 1 | EC2 작업 -> 이 인스턴스 중지 |
| 작업 2 | SNS 알림(이메일) |

`CPUCreditBalance`(잔고)가 아니라 `CPUSurplusCreditBalance`(초과분)를 쓴다. 잔고는
배포 직후 같은 정상 상황에서도 0까지 떨어지고 곧 상환되는데, 그때마다 서버가
내려가면 곤란하다. 초과분은 실제로 빌려 쓰기 시작해야 올라간다.

**SNS 이메일 구독은 확인 링크를 눌러야 살아난다.** 주제를 만들면 등록한 주소로
`AWS Notification - Subscription Confirmation` 메일이 가고, 본문의 Confirm subscription을
눌러야 상태가 `확인됨`이 된다. 안 누르면 경보가 울려도 메일이 오지 않고 서버만 조용히
꺼진다. Gmail이 스팸으로 분류하는 경우가 잦으니 스팸함도 본다. 상태는
SNS -> 주제 -> 구독 탭에서 확인한다.

**중지되면 자동으로 복구되지 않는다.** SNS 이메일을 꼭 같이 걸고, 받으면 콘솔에서
인스턴스를 다시 시작한다. 컨테이너는 `--restart unless-stopped`, Caddy도 같은
설정이라 부팅하면 알아서 올라온다.

별도로 AWS Budgets에 월 예산 알람을 하나 걸어두면 EBS·데이터 전송·ECR까지
한꺼번에 감시된다.

### 13-2. 인스턴스 상태 경보 (`tripbranch-contest-instance-status`)

OS 안에서 나는 문제(커널 패닉, 메모리 고갈, 파일시스템 손상)를 재부팅으로 푼다.

| 항목 | 값 |
| --- | --- |
| 지표 | `StatusCheckFailed_Instance` |
| 통계 / 기간 | 최대 / 1분 |
| 조건 | `> 0`, 2회 연속 |
| 누락된 데이터 처리 | 양호(정상)로 처리 |
| 작업 | EC2 작업 -> 이 인스턴스 재부팅 + SNS 알림 |
| OK 작업 | SNS 알림 — 복구됐다는 것도 알려준다 |

**시스템 장애는 이 경보가 다루지 않는다.** 호스트 하드웨어 문제는 EC2의 기본
자동 복구(`MaintenanceOptions.AutoRecovery=default`)가 이미 처리해서 다른 호스트로
옮겨준다. 따로 설정할 것이 없고, 그래서 이 경보는 인스턴스 레벨만 본다.

이 경보는 CLI로 만들 수 있었다. 13-1을 콘솔에서 만들 때 서비스 연결 역할이
생겼기 때문이다 — 순서가 반대였으면 이것도 막혔다.

```bash
aws cloudwatch put-metric-alarm --region ap-northeast-2 \
  --alarm-name tripbranch-contest-instance-status \
  --namespace AWS/EC2 --metric-name StatusCheckFailed_Instance \
  --dimensions Name=InstanceId,Value=i-017735bd69e603ad5 \
  --statistic Maximum --period 60 --evaluation-periods 2 \
  --threshold 0 --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions arn:aws:automate:ap-northeast-2:ec2:reboot \
                  arn:aws:sns:ap-northeast-2:577101745292:tripbranch-contest-alerts \
  --ok-actions arn:aws:sns:ap-northeast-2:577101745292:tripbranch-contest-alerts
```

경보 2개는 프리티어(10개) 안이라 요금이 없다.

---

# 일상 운영

## 배포

`main`에 푸시하면 바뀐 쪽만 돈다(`backend/**` -> backend, `frontend/**` -> frontend).
수동 실행은 Actions 탭의 Run workflow.

## 로그 보기

Session Manager로 접속한다.

```bash
sudo docker logs --tail 100 -f tripbranch-contest-backend
sudo docker logs --tail 50 caddy
```

**백엔드 로그의 클라이언트 IP는 `172.17.0.1`로 찍힌다** — Caddy를 거치면서
도커 게이트웨이 주소로 바뀌기 때문이다. 실제 접속자를 보려면 아래 접근 로그를
본다.

### 접근 로그 (누가 무엇을 찔렀나)

`/data/access.log`에 JSON 한 줄씩 쌓인다. 10MiB마다 회전하고 5개, 30일까지
남는다.

```bash
# 최근 요청 요약
sudo docker exec caddy tail -50 /data/access.log \
  | jq -r '[.request.client_ip, .request.method, .request.uri, .status] | @tsv'

# IP별 요청 수 — 스캐너를 찾을 때
sudo docker exec caddy cat /data/access.log \
  | jq -r .request.client_ip | sort | uniq -c | sort -rn | head

# 차단 경로를 찌른 기록만
sudo docker exec caddy cat /data/access.log \
  | jq -r 'select(.status==404) | [.request.client_ip, .request.uri] | @tsv'
```

**프론트(CloudFront)는 여기 안 남는다.** 정적 파일 요청은 EC2를 거치지 않기
때문이다. 그쪽까지 보려면 CloudFront 표준 로그를 따로 켜야 한다.

## 환경 변수 바꾸기

```bash
sudo vi /opt/tripbranch/.env
sudo docker restart tripbranch-contest-backend
```

컨테이너는 시작할 때 `--env-file`을 읽는다. 파일만 고치고 재시작하지 않으면
반영되지 않는다.

`VITE_*`는 여기가 아니라 GitHub 저장소 변수이고, **빌드 시점에 번들에 구워진다.**
바꿨으면 프론트를 다시 배포해야 한다.

## Caddy 설정 바꾸기

Caddyfile은 저장소가 아니라 서버의 `/opt/caddy/Caddyfile`에만 있다. 고친 뒤에는
컨테이너를 재시작하지 말고 reload한다 — 재시작하면 잠깐 502가 나고, reload는
무중단이다.

```bash
sudo cp /opt/caddy/Caddyfile /opt/caddy/Caddyfile.bak
sudo vi /opt/caddy/Caddyfile
sudo docker exec caddy caddy validate --config /etc/caddy/Caddyfile
sudo docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

`validate`를 먼저 돌린다. 문법이 틀린 채 reload하면 이전 설정이 그대로 남아,
고친 줄 알았는데 안 바뀐 상태가 된다.

## Langfuse — 꺼 둔다

공모전 배포는 `LANGFUSE_ENABLED`, `LANGFUSE_PROMPTS_ENABLED` 를 **둘 다 false** 로
둔다. 코드는 그대로 두고 스위치만 끈다 — 취향 RAG·사진 분위기와 같은 방식이다.
코드를 걷어내면 본 저장소에서 변경을 가져올 때마다 38개 파일에서 충돌한다.

| 스위치 | 값 | 이유 |
| --- | --- | --- |
| `LANGFUSE_ENABLED` | `false` | 트레이스를 보낼 이유가 없다 |
| `LANGFUSE_PROMPTS_ENABLED` | `false` | **아래 참고 — 이쪽이 더 중요하다** |

**프롬프트 원격 조회가 위험한 이유.** `app/prompts/loader.py` 는 이 값이 켜져
있으면 Langfuse 를 **먼저** 보고 실패했을 때만 디스크를 본다. 두 저장소가 같은
Langfuse 프로젝트를 쓰므로, 켜 두면 본 저장소 팀이 프롬프트를 고치는 순간
**배포하지 않은 공모전 서버의 답변이 바뀐다.** 심사 중에 그러면 손쓸 방법이 없다.
2026-09-20 점검에서 서버가 실제로 `true` 였던 것을 발견해 껐다.

끈 뒤에는 컨테이너를 **다시 만들어야** 반영된다. `--env-file` 은 컨테이너를 만들
때 한 번만 읽으므로 `docker restart` 로는 바뀌지 않는다(7-3 참고).

CI 의 `prompt-sync` job 도 같은 이유로 뺐다. 대조할 원격을 쓰지 않는데 job 만
남으면 프롬프트를 가져올 때마다 CI 가 빨개진다. 저장소 시크릿
`LANGFUSE_PROMPT_COMPARE` 는 되살릴 여지를 두고 지우지 않았다.

## 복구 지점 (EBS 스냅샷)

인스턴스가 복구 불가능하게 망가지면 볼륨과 함께 사라지는 것이 둘이다 —
`/opt/tripbranch/.env`와 Caddy가 발급받은 인증서(`caddy_data` 볼륨)다. 코드는
저장소에, 이미지는 ECR에 있으므로 이 둘만 스냅샷으로 받아둔다.

| 스냅샷 | 시점 |
| --- | --- |
| `snap-096939c6b5ad7d7e7` | 2026-09-19, 배포 직후 안정 상태 |

실행 중인 볼륨을 뜬 것이라 크래시 일관성 수준이다. 데이터베이스가 아니라 설정
파일과 인증서라 그 수준으로 충분하다.

### 새로 뜨는 법

```bash
# 1) 스냅샷에서 볼륨 생성 (인스턴스와 같은 AZ)
aws ec2 create-volume --region ap-northeast-2 \
  --snapshot-id snap-096939c6b5ad7d7e7 \
  --availability-zone ap-northeast-2a --volume-type gp3

# 2) 새 인스턴스를 만들고 그 볼륨을 루트로 붙이거나,
#    보조 볼륨으로 붙여 /opt/tripbranch/.env 만 꺼내온다
```

`.env`만 필요하면 스냅샷을 쓰는 것보다 **로컬 `backend/.env`에서 다시 올리는 편이
빠르다**(7-3 참고). 스냅샷은 그마저 없을 때를 위한 보험이다.

### 새로 뜰 때 잊기 쉬운 것

인스턴스를 새로 만들면 ID가 바뀐다. 다음 세 곳을 함께 고쳐야 배포가 다시 돈다.

1. 저장소 변수 `EC2_INSTANCE_ID`
2. IAM 정책 `contest-deploy`의 `SsmSendCommand` 리소스
3. 두 CloudWatch 경보의 `InstanceId` 차원

## 롤백

ECR에 커밋 SHA 태그가 남아 있다. Session Manager에서 이전 태그로 되돌린다.

```bash
REGISTRY=577101745292.dkr.ecr.ap-northeast-2.amazonaws.com
IMAGE=$REGISTRY/tripbranch-contest-backend:<이전 커밋 SHA>

aws ecr get-login-password --region ap-northeast-2 \
  | sudo docker login --username AWS --password-stdin $REGISTRY
sudo docker pull $IMAGE
sudo docker rm -f tripbranch-contest-backend
sudo docker run -d --name tripbranch-contest-backend --restart unless-stopped \
  -p 127.0.0.1:8000:8000 --env-file /opt/tripbranch/.env $IMAGE
curl -fsS http://127.0.0.1:8000/api/health
```

프론트는 이전 커밋으로 워크플로우를 다시 돌리는 게 빠르다.

## 인스턴스 유형 올리기

t3.micro가 모자라면 t3.small로 올린다. 인스턴스 중지 -> 작업 -> 인스턴스 설정 ->
인스턴스 유형 변경 -> 시작. EIP·EBS·SSM 설정이 전부 유지되고 5분이면 끝난다.
`vars.EC2_INSTANCE_ID`도 그대로다.

---

# 실측값 (2026-09-19 첫 배포)

추정이 아니라 배포 후 실제로 잰 값이다. 나중에 무언가 무거워졌는지 판단하는 기준선이다.

| 항목 | 값 |
| --- | --- |
| 백엔드 이미지 | 277,250,342 B (277MB). 원본 프로젝트의 모델 포함 이미지는 2,292,408,202 B (2.29GB)였다 — 8.3배 차이 |
| ECR 저장 크기(압축) | 90,205,369 B (90MB) |
| 앱 컨테이너 메모리 | 120.4MiB / 913MiB |
| Caddy 컨테이너 메모리 | 24MiB |
| 호스트 메모리 | 372MB 사용, 스왑 3MB |
| 디스크 | 3.6GB / 30GB |
| 평시 CPU | 0.35~0.54% (t3.micro 기준 성능 10%) |
| 배포 순간 CPU 최대 | 12.6% |
| 크레딧 잔고 | 시간당 12개씩 축적 중, 초과 크레딧 0 |
| 헬스체크 통과 | 배포 후 2회 시도(약 10초) |
| API 응답 | `/api/health` 0.05~0.18초 |
| 프론트 응답 | 0.10~0.19초 |

t3.micro로 충분하다는 판단이 이 값들로 확인됐다. 메모리는 40% 남고, CPU는 기준 성능의
1/20만 쓴다.

# 트러블슈팅

| 증상 | 확인할 것 |
| --- | --- |
| 워크플로우가 "저장소 변수가 비어 있습니다"로 멈춤 | 8단계의 Variables 등록 |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | 신뢰 정책의 `sub`가 `tripbranch-contest`인지 |
| SSM 단계에서 `InvalidInstanceId` | 인스턴스가 running인지, 인스턴스 프로파일이 붙어 있는지, 플릿 관리자에 보이는지 |
| 헬스체크 30회 실패 | `docker logs`로 부팅 오류 확인. `.env` 누락 변수일 가능성이 높다 |
| `ImportError: sentence_transformers` | `.env`에서 `TASTE_EVIDENCE_ENABLED`나 `PLACE_MOOD_ENABLED`가 켜졌다. 이미지에 모델이 없다 |
| 인증서 발급 실패 | Cloudflare 프록시가 켜졌는지(`dig`로 확인), 80번 포트가 열렸는지 |
| 브라우저 콘솔에 CORS 오류 | `.env`의 `CORS_ALLOW_ORIGINS`에 `https://contest.tripbranch.co.kr`이 있는지 |
| 음성 입력이 동작 안 함 | HTTPS로 접속했는지. `getUserMedia`는 보안 컨텍스트에서만 동작한다 |
| 프론트 새로고침하면 XML 오류 | CloudFront 오류 페이지 403/404 -> `/index.html` 200 설정 |
| 새 배포가 브라우저에 안 보임 | CloudFront 무효화 완료 여부. `curl -I`로 `index.html`과 `sw.js`의 `Cache-Control`이 `no-cache`인지 본다. `immutable`이면 다시 올려야 한다 |
| 채팅이 429로 거부됨 | 레이트 리밋에 걸렸다. 기본 20회/60초, IP별이다. `.env`의 `RATE_LIMIT_REQUESTS`로 조정하고 컨테이너를 재시작한다 |
| 사이트가 통째로 죽음 | 크레딧 알람이 인스턴스를 중지시켰을 수 있다. EC2 상태 확인 |
