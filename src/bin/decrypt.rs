use txtcrypt::cli::{self, die, Sink, Source};
use txtcrypt::{assemble, clip, decrypt, AssembleError};

fn main() {
    let args = cli::parse_args(
        "decrypt",
        "encrypt 가 만든 암호문 텍스트를 원래 문자열로 되돌립니다.",
        false,
    );
    let source = args.source();
    let mut text = String::from_utf8_lossy(&cli::read_source(&source)).into_owned();
    let token = loop {
        match assemble(&text) {
            Ok(token) => break token,
            Err(AssembleError::Invalid(e)) => die(&e),
            Err(AssembleError::Missing(missing, total)) => {
                let list: Vec<String> = missing.iter().map(|i| i.to_string()).collect();
                let need = format!("전체 {total}조각 중 {}번 조각이 없습니다.", list.join(", "));
                // 클립보드로 받는 중이면 나머지 조각을 차례로 복사해 넣게 한다.
                if source != Source::Clipboard
                    || !cli::wait_enter(&format!("{need}\n해당 조각을 복사한 뒤 Enter 를 누르세요..."))
                {
                    die(&need);
                }
                text.push('\n');
                text.push_str(&clip::get_text().unwrap_or_else(|e| die(&e)));
            }
        }
    };
    let plain = decrypt(&token, &args.passphrase).unwrap_or_else(|e| die(&e));

    match args.sink(&source) {
        Sink::File(p) => cli::write_file(&p, &plain),
        Sink::Stdout => cli::write_stdout(&plain),
        Sink::Clipboard => {
            let s = std::str::from_utf8(&plain).unwrap_or_else(|_| {
                die("복호화 결과가 UTF-8 텍스트가 아니어서 클립보드에 넣을 수 없습니다. -o 로 저장하세요")
            });
            cli::copy(s);
            eprintln!("복호화한 {}자를 클립보드에 복사했습니다.", s.chars().count());
        }
    }
    cli::finish(0);
}
