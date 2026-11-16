use txtcrypt::cli::{self, die, Sink};
use txtcrypt::{encrypt, split_token};

fn main() {
    let args = cli::parse_args(
        "encrypt",
        "문자열(쿼리, 프로시저 등)을 암호화해 텍스트로 출력합니다.",
        true,
    );
    let source = args.source();
    let plain = cli::read_source(&source);
    let token = encrypt(&plain, &args.passphrase).unwrap_or_else(|e| die(&e));
    let pieces = match args.split {
        Some(max) => split_token(&token, max),
        None => vec![token],
    };
    let total = pieces.len();
    let chars: usize = pieces.iter().map(String::len).sum();
    let summary = if total > 1 {
        format!("암호문 {chars}자, {total}조각")
    } else {
        format!("암호문 {chars}자")
    };

    match args.sink(&source) {
        Sink::Clipboard => {
            eprintln!("{summary}");
            for (i, piece) in pieces.iter().enumerate() {
                cli::copy(piece);
                if total == 1 {
                    eprintln!("클립보드에 복사했습니다.");
                    break;
                }
                eprintln!("조각 {}/{total} ({}자)을 클립보드에 복사했습니다.", i + 1, piece.len());
                if i + 1 < total && !cli::wait_enter("붙여넣은 뒤 Enter 를 누르면 다음 조각을 복사합니다...") {
                    die("여러 조각을 클립보드로 차례로 복사하려면 키보드 입력이 필요합니다. -o 를 쓰세요");
                }
            }
        }
        sink => {
            let text = pieces.join("\n\n") + "\n";
            match sink {
                Sink::File(p) => cli::write_file(&p, text.as_bytes()),
                _ => cli::write_stdout(text.as_bytes()),
            }
            eprintln!("{summary}");
        }
    }
    cli::finish(0);
}
