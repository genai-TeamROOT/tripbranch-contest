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
| ECR | `tripbranch-contest-backend` | |
| IAM 역할 | `github-actions-deploy-contest` | OIDC 공급자는 계정에 이미 있는 것을 재사용 |
| 보안 그룹 | `tripbranch-contest-sg` | 인바운드 80/443만 |
| EC2 | `<INSTANCE_ID>` | t3.micro, AL2023 x86_64, 30GB gp3 |
| EIP | `<ELASTIC_IP>` | |
| S3 | `<BUCKET>` | 퍼블릭 액세스 차단 유지 |
| CloudFront | `<DIST_ID>` | |
| ACM | `<CERT_ARN>` | **us-east-1** |
| EC2 인스턴스 프로파일 | `tripbranch-ec2-role` | 본 프로젝트와 공유(SSM Core + ECR ReadOnly 읽기 전용) |

구축하면서 `<...>` 자리를 채운다.

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

`<INSTANCE_ID>`, `<BUCKET>`, `<DIST_ID>`를 채운 뒤 인라인 정책으로 붙인다.
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
        "arn:aws:ec2:ap-northeast-2:577101745292:instance/<INSTANCE_ID>",
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
      "Resource": "arn:aws:s3:::<BUCKET>"
    },
    {
      "Sid": "S3FrontendObjects",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<BUCKET>/*"
    },
    {
      "Sid": "CloudFrontInvalidate",
      "Effect": "Allow",
      "Action": "cloudfront:CreateInvalidation",
      "Resource": "arn:aws:cloudfront::577101745292:distribution/<DIST_ID>"
    }
  ]
}
```

S3·CloudFront 부분은 9단계에서 리소스를 만든 뒤에 채워도 된다. 백엔드 배포만
먼저 돌리려면 ECR·SSM 구문만 있어도 동작한다.

## 3. 보안 그룹

`tripbranch-contest-sg`, VPC는 기본 VPC(`vpc-0185215c2cb04ae38`).

| 방향 | 포트 | 소스 |
| --- | --- | --- |
| 인바운드 | 80 (TCP) | `0.0.0.0/0` |
| 인바운드 | 443 (TCP) | `0.0.0.0/0` |

**22번(SSH)은 열지 않는다.** 접속은 SSM으로 한다. 80번은 Let's Encrypt의
HTTP-01 챌린지와 HTTPS 리디렉트에 쓰인다.

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
| A | `api-contest` | `<ELASTIC_IP>` | **DNS only (회색 구름)** |

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

## 8. GitHub 저장소 변수

Settings -> Secrets and variables -> Actions -> **Variables** 탭에 등록한다.
전부 Secrets가 아니라 Variables다 — `VITE_*`는 브라우저로 내려가는 값이라
애초에 비밀이 아니고, 나머지는 리소스 ID다.

| 이름 | 값 | 쓰는 워크플로우 |
| --- | --- | --- |
| `EC2_INSTANCE_ID` | `<INSTANCE_ID>` | backend |
| `S3_BUCKET` | `<BUCKET>` | frontend |
| `CLOUDFRONT_DISTRIBUTION_ID` | `<DIST_ID>` | frontend |
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

- 버킷 이름 `<BUCKET>`, 리전 `ap-northeast-2`
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

### Cloudflare CNAME

| 형식 | 이름 | 값 | 프록시 |
| --- | --- | --- | --- |
| CNAME | `contest` | `<CloudFront 배포 도메인>` | **DNS only** |

## 12. 프론트 첫 배포

Actions -> Deploy frontend -> Run workflow.

## 13. 크레딧 알람

CloudWatch -> 경보 -> 경보 생성.

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

**중지되면 자동으로 복구되지 않는다.** SNS 이메일을 꼭 같이 걸고, 받으면 콘솔에서
인스턴스를 다시 시작한다. 컨테이너는 `--restart unless-stopped`, Caddy도 같은
설정이라 부팅하면 알아서 올라온다.

별도로 AWS Budgets에 월 예산 알람을 하나 걸어두면 EBS·데이터 전송·ECR까지
한꺼번에 감시된다.

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

## 환경 변수 바꾸기

```bash
sudo vi /opt/tripbranch/.env
sudo docker restart tripbranch-contest-backend
```

컨테이너는 시작할 때 `--env-file`을 읽는다. 파일만 고치고 재시작하지 않으면
반영되지 않는다.

`VITE_*`는 여기가 아니라 GitHub 저장소 변수이고, **빌드 시점에 번들에 구워진다.**
바꿨으면 프론트를 다시 배포해야 한다.

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
| 새 배포가 브라우저에 안 보임 | CloudFront 무효화 완료 여부, `index.html`의 `Cache-Control` |
| 사이트가 통째로 죽음 | 크레딧 알람이 인스턴스를 중지시켰을 수 있다. EC2 상태 확인 |
