//! 두 실행 파일이 공유하는 명령행 처리와 입출력.

use crate::{clip, DEFAULT_PASSPHRASE, KEY_ENV};
use std::io::{BufRead, IsTerminal, Read, Write};

pub struct Args {
    pub passphrase: String,
    pub input: Option<String>,
    pub output: Option<String>,
    pub copy: bool,
    pub split: Option<usize>,
}

pub const MIN_SPLIT: usize = 16;

/// `allow_split` 이면 -s 옵션을 받는다 (encrypt 전용).
pub fn parse_args(name: &str, what: &str, allow_split: bool) -> Args {
    let mut args = Args {
        passphrase: String::new(),
        input: None,
        output: None,
        copy: false,
        split: None,
    };
    let mut key = None;
    let mut it = std::env::args().skip(1);
    let value = |opt: &str, it: &mut dyn Iterator<Item = String>| {
        it.next().unwrap_or_else(|| die(&format!("{opt} 뒤에 값이 필요합니다")))
    };
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" | "/?" => {
                let split = if allow_split {
                    "\n\x20 -s 글자수  암호문을 한 조각이 이 글자수를 넘지 않게 나눔 (예: -s 10000)"
                } else {
                    ""
                };
                let opt = if allow_split { " [-s 글자수]" } else { "" };
                println!(
                    "사용법: {name} [-k 키] [-o 출력파일] [-c]{opt} [입력파일]\n\n\
                     {what}\n\n\
                     \x20 입력파일   생략하면 파이프 입력, 그것도 없으면 클립보드에서 읽습니다\n\
                     \x20 -o 파일    결과를 파일로 저장\n\
                     \x20 -c         결과를 클립보드로 복사 (클립보드에서 읽었으면 기본값)\n\
                     \x20 -k 키      암호 키. 생략하면 환경변수 {KEY_ENV}, 그것도 없으면 내장 키 사용{split}"
                );
                finish(0);
            }
            "-k" | "--key" => key = Some(value("-k", &mut it)),
            "-o" | "--out" => args.output = Some(value("-o", &mut it)),
            "-c" | "--copy" => args.copy = true,
            "-s" | "--split" if allow_split => {
                let n = value("-s", &mut it).parse().ok().filter(|&n| n >= MIN_SPLIT);
                args.split =
                    Some(n.unwrap_or_else(|| die(&format!("-s 값은 {MIN_SPLIT} 이상의 숫자여야 합니다"))));
            }
            _ if args.input.is_none() && !a.starts_with('-') => args.input = Some(a),
            _ => die(&format!("알 수 없는 인자: {a} (-h 로 사용법 확인)")),
        }
    }
    args.passphrase = key
        .or_else(|| std::env::var(KEY_ENV).ok().filter(|s| !s.is_empty()))
        .unwrap_or_else(|| DEFAULT_PASSPHRASE.to_string());
    args
}

#[derive(PartialEq)]
pub enum Source {
    File(String),
    Stdin,
    Clipboard,
}

pub enum Sink {
    File(String),
    Clipboard,
    Stdout,
}

impl Args {
    pub fn source(&self) -> Source {
        match &self.input {
            Some(p) => Source::File(p.clone()),
            None if clip::SUPPORTED && std::io::stdin().is_terminal() => Source::Clipboard,
            None => Source::Stdin,
        }
    }

    pub fn sink(&self, source: &Source) -> Sink {
        match &self.output {
            Some(p) => Sink::File(p.clone()),
            None if self.copy || *source == Source::Clipboard => Sink::Clipboard,
            None => Sink::Stdout,
        }
    }
}

pub fn read_source(source: &Source) -> Vec<u8> {
    match source {
        Source::File(p) => std::fs::read(p).unwrap_or_else(|e| die(&format!("{p} 읽기 실패: {e}"))),
        Source::Clipboard => clip::get_text().unwrap_or_else(|e| die(&e)).into_bytes(),
        Source::Stdin => {
            let stdin = std::io::stdin();
            if stdin.is_terminal() {
                eprintln!("내용을 붙여넣고 새 줄에서 Ctrl+D 를 누르세요:");
            }
            let mut buf = Vec::new();
            stdin
                .lock()
                .read_to_end(&mut buf)
                .unwrap_or_else(|e| die(&format!("입력 읽기 실패: {e}")));
            buf
        }
    }
}

pub fn write_file(path: &str, data: &[u8]) {
    std::fs::write(path, data).unwrap_or_else(|e| die(&format!("{path} 쓰기 실패: {e}")))
}

pub fn write_stdout(data: &[u8]) {
    let mut out = std::io::stdout().lock();
    if out.write_all(data).and_then(|_| out.flush()).is_err() {
        die("화면 출력 실패 (UTF-8이 아닌 내용일 수 있음). -o 로 파일에 저장하세요");
    }
}

pub fn copy(text: &str) {
    clip::set_text(text).unwrap_or_else(|e| die(&e))
}

/// 안내문을 보여주고 Enter 를 기다린다. 키보드 입력을 받을 수 없으면 false.
pub fn wait_enter(prompt: &str) -> bool {
    let stdin = std::io::stdin();
    if !stdin.is_terminal() {
        return false;
    }
    eprint!("{prompt}");
    let mut line = String::new();
    matches!(stdin.lock().read_line(&mut line), Ok(n) if n > 0)
}

/// 종료. 더블클릭으로 실행된 경우 창이 바로 닫히지 않게 Enter 를 기다린다.
pub fn finish(code: i32) -> ! {
    if clip::owns_console() {
        wait_enter("\nEnter 키를 누르면 종료합니다...");
    }
    std::process::exit(code);
}

pub fn die(msg: &str) -> ! {
    eprintln!("오류: {msg}");
    finish(1);
}
