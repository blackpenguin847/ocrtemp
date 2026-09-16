# ocrlab

견적 원가 산출서 PDF 하나를 **여러 추출 방식으로 각각 돌려 결과를 비교**하는 로컬 실험 도구입니다.
어떤 방식이 내 문서에 맞는지 고르기 위한 것이므로, 엔진끼리 섞지 않고 **한 번에 하나씩** 적용한 결과를 따로 내놓습니다.

- 입력: PDF 한 개
- 출력: 엔진별 JSON (`out/<엔진>.json`) + 비교 요약 (`out/summary.json`)
- 전부 로컬에서 처리합니다. 외부 API 호출이 없습니다.

## 엔진

| 엔진 | 방식 | OCR | LLM |
|---|---|---|---|
| `pdftext` | PDF 내장 텍스트 레이어 + 좌표 기반 표 재구성 | — | — |
| `tesseract` | Tesseract OCR + 좌표 기반 표 재구성 | ✓ | — |
| `tesseract-llm` | Tesseract 원문 → LLM 구조화 (이미지 안 보냄) | ✓ | ✓ |
| `vision` | 이미지 → vision LLM | — | ✓ |
| `vision-ocr` | 이미지 + Tesseract 원문 → vision LLM (하이브리드) | ✓ | ✓ |

각 엔진의 성격은 이렇게 갈립니다.

- **`pdftext`** 는 기준선입니다. 결과가 0건이면 그 PDF에 텍스트 레이어가 없다는 뜻(= 스캔본)이므로, 나머지 엔진을 쓸 이유가 생깁니다. 텍스트 레이어가 있으면 가장 빠르고 정확합니다.
- **`tesseract`** 는 LLM 없이 완전히 결정적입니다. 빠르고 재현 가능하지만 표 머리글이 깨지면 열 배정이 어긋납니다.
- **`tesseract-llm`** 은 이미지를 보내지 않으므로 **vision 모델이 없어도** 동작합니다. 텍스트 전용 모델로 쓸 수 있습니다.
- **`vision`** 은 표 구조와 문맥에 강하지만 숫자를 자주 틀립니다.
- **`vision-ocr`** 은 숫자에 강한 OCR 원문을 함께 주되 판단 기준은 이미지로 둡니다.

`pdftext` 와 `tesseract` 는 **같은 표 재구성 코드**(`layout.py`)를 씁니다. 두 엔진의 차이는 글자와 좌표를 어디서 얻었는지 하나뿐이라, 비교가 공정합니다.

## 요구 사항

- Python 3.10+
- (선택) [Ollama](https://ollama.com) — LLM 엔진용
- (선택) Tesseract — OCR 엔진용

없는 도구가 있어도 괜찮습니다. 메뉴가 엔진마다 준비 상태와 해결 방법을 알려주고, 쓸 수 없는 엔진은 건너뜁니다.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .

# OCR 엔진을 쓰려면
sudo apt install tesseract-ocr tesseract-ocr-kor    # macOS: brew install tesseract tesseract-lang
pip install -e ".[tesseract]"

# LLM 엔진을 쓰려면 (VRAM 8GB 기준)
ollama pull qwen3-vl:8b
```

## 사용법

```bash
# 메뉴에서 기능을 골라 실행
ocrlab 견적서.pdf

# 비대화형: 엔진 지정
ocrlab 견적서.pdf -e all
ocrlab 견적서.pdf -e tesseract,vision
ocrlab 견적서.pdf -e pdftext --pages 1,3,5-7

# 스캔 품질이 낮을 때
ocrlab 견적서.pdf -e all --dpi 300
```

실행하면 이런 메뉴가 뜹니다.

```
사용할 기능을 고르세요:

  1) ○ pdftext
       PDF 내장 텍스트 레이어 (OCR·LLM 없음, 스캔본이면 0건)
       PyMuPDF 1.28.2

  2) ○ tesseract
       Tesseract OCR + 좌표 기반 표 재구성 (LLM 없음)
       tesseract 5.3.4 · kor+eng

  3) × vision
       이미지 → vision LLM → JSON (Ollama)
       → Ollama 서버(http://localhost:11434) — 연결이 거부되었습니다. `ollama serve` 가 실행 중인지 확인하세요.

  번호를 쉼표로 (예: 1,2,4)   a=전체   q=종료
```

끝나면 비교표가 나옵니다.

```
엔진           상태  소요  페이지  품목  금액합계  칸채움  계산일치
─────────────  ────  ────  ──────  ────  ────────  ──────  ────────
pdftext        OK    0.0s       1     0         -       -         -
tesseract      OK    0.9s       1     4   182,400     75%      100%
tesseract-llm  OK    9.7s       1     4   182,400     88%      100%
vision         OK   41.2s       1     4   182,400     93%       75%
vision-ocr     OK   44.0s       1     4   182,400     93%      100%
```

| 지표 | 뜻 |
|---|---|
| 품목 | 뽑아낸 행 수 |
| 금액합계 | 품목 금액의 합. 엔진끼리 다르면 어느 쪽이 틀린 것 |
| 칸채움 | 품목의 칸(품명·규격·단위·수량·단가·금액…)이 얼마나 채워졌는지 |
| 계산일치 | `수량 × 단가 = 금액` 이 맞는 행의 비율. **숫자 오인식을 잡아내는 지표** |

`계산일치` 가 낮으면 숫자를 잘못 읽은 것이고, `칸채움` 이 낮으면 열을 놓친 것입니다. 두 지표가 갈리는 지점이 곧 엔진을 고르는 기준이 됩니다.

## 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `-e, --engine` | (메뉴) | 실행할 엔진. 쉼표로 여러 개, `all` = 전체 |
| `-o, --output` | `out` | 결과 저장 폴더 |
| `--pages` | 전체 | `1,3,5-7` 형식 |
| `--dpi` | `200` | PDF 렌더링 해상도 |
| `--max-edge` | `1600` | 모델에 보낼 이미지 최대 변(px) |
| `--no-enhance` | off | 대비 보정 끄기 |
| `-m, --model` | `qwen3-vl:8b` | Ollama 모델 |
| `--host` | `http://localhost:11434` | Ollama 주소 |
| `--num-ctx` | `8192` | 모델 컨텍스트 길이 |
| `--timeout` | `600` | 응답 타임아웃(초) |
| `--lang` | `kor+eng` | Tesseract 언어 |
| `--psm` | `4` | Tesseract 페이지 분할 모드 |

> `--psm 4`(가변 크기 단일 컬럼)가 견적서 표에 가장 잘 맞습니다. 기본값인 `--psm 6`(균일 블록)은 표의 행을 통째로 날리는 경우가 있어 바꿔 두었습니다.

## 출력 JSON

모든 엔진이 **같은 구조**로 결과를 냅니다. 그래야 나란히 놓고 볼 수 있습니다.

```json
{
  "엔진": "tesseract",
  "입력": "견적서.pdf",
  "성공": true,
  "실행조건": { "dpi": 300, "lang": "kor+eng", "psm": 4 },
  "지표": { "품목수": 4, "금액합계": 182400, "칸채움률": 0.75, "계산일치율": 1.0, "소요초": 0.9 },
  "페이지": [
    {
      "페이지": 1,
      "신뢰도": 88.7,
      "필드": {
        "문서번호": "Q-2024-001",
        "업체명": "가나산업",
        "원가": { "재료비": 182400, "노무비": 400000 },
        "합계": 812400
      },
      "품목": [
        { "구분": "", "품명": "강판", "규격": "SS400 3.2T", "단위": "㎡",
          "수량": 2, "단가": 35000, "금액": 70000, "비고": "" }
      ],
      "메모": [],
      "원문": "..."
    }
  ]
}
```

- `필드.원가` 에는 비목(재료비·노무비·경비·관리비·포장비·영업이익)이 들어갑니다. **비목 집계 행은 품목으로 세지 않습니다** — 그래야 금액합계가 이중으로 잡히지 않습니다.
- `원문` 은 그 엔진이 실제로 확보한 텍스트입니다. 결과가 이상할 때 여기부터 보세요.
- `메모` 에는 "텍스트 레이어가 없습니다", "표 머리글을 찾지 못했습니다" 같은 경고가 들어갑니다.

## 구조

```
src/ocrlab/
├─ cli.py           # 진입점, 인자 파싱
├─ menu.py          # 기능 선택 화면
├─ schema.py        # 모든 엔진이 공유하는 결과 구조 + 비교 지표
├─ layout.py        # 단어 박스 → 표 → 품목 (규칙 기반, LLM 없음)
├─ render.py        # PDF → 이미지 (그레이스케일·대비보정·리사이즈)
├─ ollama.py        # Ollama 클라이언트, 관대한 JSON 파싱
├─ prompts.py       # LLM 프롬프트
├─ parsing.py       # LLM JSON → 공통 스키마
├─ report.py        # JSON 저장 + 비교표
└─ engines/
   ├─ base.py       # Engine 인터페이스
   ├─ pdftext.py    # PDF 텍스트 레이어
   ├─ tesseract.py  # Tesseract OCR
   ├─ llm.py        # vision / tesseract-llm / vision-ocr
   └─ __init__.py   # 엔진 등록소
```

라이브러리로도 쓸 수 있습니다.

```python
from ocrlab import Options, build

options = Options(dpi=300, pages=[1])
result = build("tesseract", options).run("견적서.pdf")

print(result.metrics())          # {'품목수': 4, '금액합계': 182400, ...}
for item in result.items:
    print(item.name, item.amount)
```

## 엔진 추가하기

`Engine` 을 상속해 `check()` 와 `extract()` 를 쓰고, `engines/__init__.py` 의 `ENGINES` 에 넣으면 됩니다.
메뉴·결과 파일·비교표는 자동으로 따라옵니다.

```python
class MyEngine(Engine):
    name = "my-engine"
    description = "한 줄 설명"

    def check(self) -> Availability:
        return Availability(ok=True, detail="준비됨")

    def extract(self, pdf: Path) -> list[PageResult]:
        ...
```

## 개발

```bash
pip install -e ".[dev]"
pytest
```

테스트는 Ollama 없이 돌아갑니다(가짜 클라이언트 사용). Tesseract가 설치돼 있지 않으면 관련 테스트는 건너뜁니다.
스캔본 PDF와 디지털 PDF를 테스트 안에서 만들어 쓰므로 표본 파일이 필요 없습니다.

## 라이선스

MIT
