# estimate-ocr

스캔된 견적 원가명세서 PDF를 **로컬 Ollama vision 모델**로 읽어 구조화된 데이터(JSON / CSV / XLSX)로 변환하는 CLI 도구입니다. 외부 API 호출 없이 전부 로컬에서 처리합니다.

## 요구 사항

- Python 3.10+
- [Ollama](https://ollama.com) (로컬 실행 중)
- VRAM 8GB 이상 권장

## 설치

```bash
# 1) Ollama 모델 준비 (VRAM 8GB 기준)
ollama pull qwen3-vl:8b

# 2) 패키지 설치
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .

# 3) (권장) Tesseract OCR 보조
sudo apt install tesseract-ocr tesseract-ocr-kor    # macOS: brew install tesseract tesseract-lang
pip install -e ".[tesseract]"
```

Tesseract는 선택 사항이지만 **설치를 권합니다.** vision 모델이 숫자를 잘못 읽는 것을 잡아내고, 모델이 아예 동작하지 않을 때 대체 경로가 됩니다. 설치돼 있지 않으면 조용히 비활성화됩니다.

### 모델 선택

| 모델 | 크기 | 비고 |
|---|---|---|
| `qwen3-vl:8b` | ~6GB | **기본값.** 한국어/CJK OCR에 가장 강함 |
| `minicpm-v` | ~5.5GB | 문서 OCR 특화, 더 가벼움. 비교용으로 유용 |
| `llama3.2-vision:11b` | ~8GB | 8GB VRAM에서는 빠듯함. `--max-edge 1280` 권장 |

여러 모델로 같은 PDF를 돌려 결과를 비교한 뒤 고르는 것을 권합니다.

## 사용법

```bash
# 기본: 폴더 안의 모든 PDF → out/result.xlsx
estimate-ocr ./samples -o out

# 단일 파일, 모든 형식으로 출력
estimate-ocr 견적서_2024.pdf -f all

# 특정 페이지만
estimate-ocr 견적서.pdf --pages 1,3,5-7

# 다른 모델 사용
estimate-ocr ./samples -m minicpm-v

# VRAM 부족(OOM)이거나 너무 느릴 때
estimate-ocr ./samples --max-edge 1280 --num-ctx 4096

# 인식 정확도만 먼저 확인 (구조화 없이 원문 텍스트만)
estimate-ocr 견적서.pdf --raw-text -o out

# 전처리된 이미지 확인 (out/_debug/ 에 저장)
estimate-ocr 견적서.pdf --debug-images -v

# vision 모델이 전혀 동작하지 않을 때: Tesseract 로 읽고 LLM 은 구조화만
estimate-ocr 견적서.pdf --ocr only

# 숫자 정확도가 중요할 때: OCR 원문을 모델에 함께 제공
estimate-ocr 견적서.pdf --ocr assist
```

## Tesseract 보조 (`--ocr`)

로컬 vision 모델과 Tesseract는 서로 반대 방향으로 강합니다.

| | vision 모델 | Tesseract |
|---|---|---|
| 표 구조·열 매핑 | **강함** | 약함 (행만 보존) |
| 문맥 이해 (비목 분류 등) | **강함** | 없음 |
| 인쇄체 숫자 | 자주 틀림 | **강함** |
| 규격 같은 영숫자 코드 | **강함** | 자주 틀림 (`SS400` → `$S400`) |
| 속도 | 느림 | 빠름 |

그래서 기본값(`auto`)은 **둘을 겹쳐서** 씁니다.

| 모드 | 동작 |
|---|---|
| `auto` (기본) | 페이지마다 OCR을 함께 돌려 **숫자를 교차검증**하고, vision 경로가 모두 실패하면 OCR 원문으로 구조화합니다. Tesseract가 없으면 자동으로 꺼집니다 |
| `assist` | `auto`에 더해 1차 프롬프트에 OCR 원문을 함께 넣습니다. 판단 기준은 이미지이고 원문은 자릿수 참고용입니다 |
| `only` | vision을 쓰지 않고 OCR 원문만 LLM으로 구조화합니다. vision 모델이 동작하지 않을 때의 탈출구이며, 텍스트 전용 모델로도 돌아갑니다 |
| `off` | 쓰지 않습니다 |

`only`는 표 구조를 Tesseract에 전적으로 의존하므로 규격 열이 틀리거나 행을 놓칠 수 있습니다. **정상 경로는 `auto`/`assist`이고 `only`는 대체 수단입니다.**

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `-o, --output` | `out` | 결과 저장 폴더 |
| `-f, --format` | `xlsx` | `json` / `csv` / `xlsx` / `all` |
| `-m, --model` | `qwen3-vl:8b` | Ollama 모델명 |
| `--host` | `http://localhost:11434` | Ollama 서버 주소 |
| `--dpi` | `200` | PDF 렌더링 해상도 |
| `--max-edge` | `1600` | 이미지 최대 변 길이(px). VRAM 부족 시 낮출 것 |
| `--num-ctx` | `8192` | 모델 컨텍스트 길이 |
| `--timeout` | `600` | 응답 타임아웃(초) |
| `--pages` | 전체 | `1,3,5-7` 형식 |
| `--raw-text` | off | 구조화 없이 원문만 추출 |
| `--ocr` | `auto` | Tesseract 보조: `off` / `auto` / `assist` / `only` |
| `--ocr-lang` | `kor+eng` | Tesseract 언어 |
| `--ocr-psm` | `4` | Tesseract 페이지 분할 모드 |
| `--no-enhance` | off | 대비 보정 끄기 |

## 출력 결과

`out/result.xlsx`

- **추출결과 시트**: 한 행 = 명세서의 한 품목. 원본 PDF명, 페이지, 문서번호, 구분(비목), 품명, 규격, 단위, 수량, 단가, 금액, 비고
- **원가요약 시트**: 한 행 = 문서 하나. 비목별 금액과 합계
- **검수 시트**: 자동 검증에서 걸린 경고 목록

### 비목(원가 항목)

견적 원가 산출서의 집계 항목을 원가계산 순서대로 다음 6개 비목으로 모읍니다.

| 비목 | 문서에서 쓰이는 다른 이름 (자동 인식) |
|---|---|
| **재료비** | 자재비, 직접/간접재료비, 부품비, material |
| **노무비** | 인건비, 노임, 직접/간접노무비, 가공비, labor |
| **경비** | 제경비, 직접/간접경비, 기계경비, expense |
| **관리비** | 일반관리비, 간접비, overhead, admin |
| **포장비** | 포장료, 포장및운반비, packing |
| **영업이익** | 이윤, 이익, 기업이윤, profit |

- **경비와 관리비(일반관리비)는 별개의 비목으로 따로 집계합니다.** 문서에 하나만 있으면 그 하나만 채워집니다.
- `재료비 | 1,000,000` 처럼 품목 표에 섞여 있는 **집계 행은 품목으로 세지 않고** 원가요약으로 옮깁니다. (품목 합계가 이중으로 잡히는 것을 막습니다)
- 품목에 `구분` 열이 있으면 비목별로 묶어 원가요약과 대조합니다.
- 별칭 표는 `models.py`의 `CATEGORY_ALIASES`, 비목 목록은 같은 파일의 `COST_CATEGORIES`에 있습니다. 프롬프트와 출력 시트의 열은 이 목록에서 생성되므로, 비목을 더하거나 빼면 나머지가 따라 바뀝니다.

### 자동 검증

- `수량 × 단가 ≠ 금액` 인 행
- 품명이 비어 있는 행
- **비목 합계와 문서상 합계의 불일치**
- **비목별 품목 합계와 원가요약 금액의 불일치**
- **표준 비목에 속하지 않는 구분값 / 원가 항목**
- 품목 금액 합계와 문서상 소계의 불일치 (원가요약이 없는 문서)
- **모델이 읽은 단가·금액이 Tesseract OCR 원문에 없는 행** (`--ocr` 사용 시)
- JSON 파싱 실패 / 품목 0건

비목이 비어 있다는 사실 자체는 경고하지 않습니다 — 경비나 포장비가 없는 문서는 흔하기 때문입니다. 다만 **합계가 맞지 않을 때는** 못 읽은 비목을 차액과 함께 알려줍니다.

문제가 있는 행은 xlsx에서 **노란색으로 표시**되고 사유가 셀 메모로 붙으므로, 해당 행만 원본 스캔과 대조하면 됩니다.

## 스캔 PDF에서 아무것도 안 나올 때

페이지에 글자 없이 이미지 객체만 있는 스캔본은 이 도구의 기본 대상입니다. 그래도 결과가 비면 아래 순서로 좁히세요.

1. **`--debug-images -v` 로 전처리 결과부터 봅니다.** `out/_debug/`의 PNG가 새까맣거나 새하야면 모델이 아니라 이미지 문제입니다. 로그에 `거의 백지입니다` 같은 경고가 함께 뜹니다.
   - 페이지가 뭉개져 보이면 `--no-enhance`로 대비 보정을 끄고 비교해 보세요.
2. **`--ocr only` 로 돌려봅니다.** 결과가 나오면 PDF·렌더링은 정상이고 **vision 모델 쪽 문제**입니다. 모델이 이미지를 실제로 받고 있는지(비전 지원 모델인지), VRAM이 모자라지 않은지 확인하세요. `--max-edge 1280 --num-ctx 4096`으로 낮춰보는 것도 방법입니다.
3. **`--raw-text` 로 원문만 뽑아 봅니다.** `--ocr auto`면 Tesseract 결과가 `*_ocr.txt`로 함께 저장되므로, 모델이 읽은 원문과 OCR 원문을 나란히 비교할 수 있습니다.
4. **글자가 작으면 `--dpi 300`**으로 올리세요. 200dpi에서 작은 글씨는 획이 뭉갭니다. 고해상도 스캔이라면 `--max-edge`도 함께 키워야 축소로 획이 날아가지 않습니다.
5. 암호가 걸린 PDF는 읽을 수 없습니다. 암호를 푼 사본으로 시도하세요.

> 참고: 0.1.x 초기 버전에는 대비 보정이 스캔본을 새까맣게 뭉개는 버그가 있었습니다. 스캔 PDF에서 인식이 전혀 안 됐다면 최신 버전으로 다시 시도해 보세요.

## 정확도를 높이는 방법

1. **먼저 `--raw-text`로 확인.** 원문 텍스트조차 제대로 안 읽히면 프롬프트가 아니라 이미지 품질 문제입니다. `--dpi 300`으로 올려보세요.
2. **`--debug-images`로 전처리 결과 확인.** 스캔이 기울어졌거나 너무 어두우면 인식률이 크게 떨어집니다.
3. **양식이 조금씩 다른 경우**, `src/estimate_ocr/prompts.py`의 `USER_PROMPT` 스키마에 실제 문서에서 자주 쓰이는 열 이름을 추가하세요. `models.py`의 `pick()` 키 목록에도 함께 추가하면 매핑됩니다. 비목 이름이 다르게 적히는 경우는 `models.py`의 `CATEGORY_ALIASES`에 추가하면 됩니다.
4. 로컬 vision 모델은 클라우드 OCR보다 숫자 오인식이 잦습니다. **검수 시트를 반드시 확인하는 워크플로**를 전제로 사용하세요. Tesseract를 설치해 두면 숫자 교차검증이 이 확인을 크게 덜어줍니다.
5. 대비 보정은 문서 스캔을 전제로 합니다. 글자가 페이지의 1% 남짓만 차지한다는 가정이므로, 사진이 대부분인 페이지에서는 `--no-enhance`가 나을 수 있습니다.

## 구조

```
src/estimate_ocr/
├─ cli.py            # CLI 진입점, 인자 파싱
├─ extractor.py      # 파이프라인 오케스트레이션 + fallback 재시도
├─ ollama_client.py  # Ollama HTTP 클라이언트, JSON 관대 파싱
├─ pdf_render.py     # PDF → 이미지 (그레이스케일/대비보정/리사이즈)
├─ tesseract_ocr.py  # Tesseract 보조 OCR + 숫자 교차검증용 추출
├─ prompts.py        # 프롬프트 템플릿
├─ models.py         # 데이터 모델 + 비목 매핑 + 자동 검증 로직
└─ exporter.py       # JSON / CSV / XLSX 출력
```

라이브러리로도 사용 가능합니다.

```python
from estimate_ocr import OllamaClient, Extractor

client = OllamaClient(model="qwen3-vl:8b")
docs = Extractor(client).extract_pdf("견적서.pdf")

for doc in docs:
    print(doc.page, len(doc.items), doc.validate())
    print(doc.costs.material, doc.costs.labor, doc.costs.profit)  # 재료비 / 노무비 / 영업이익
    print(doc.costs.expense, doc.costs.overhead)                  # 경비 / 관리비
    print(doc.costs.to_dict())      # 비목별 원가 요약
    print(doc.category_totals())    # 품목을 비목별로 합산한 값
```

## 개발

```bash
pip install -e ".[dev]"
pytest
```

테스트는 Ollama 없이 동작합니다. 가짜 클라이언트로 렌더링 → 추출 → fallback 재시도 → 출력까지의 경로를 검증합니다.

## 라이선스

MIT
