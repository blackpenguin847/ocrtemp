use txtcrypt::{die, encrypt, parse_args, read_input, write_output};

fn main() {
    let args = parse_args("encrypt", "문자열(쿼리, 프로시저 등)을 암호화해 텍스트로 출력합니다.");
    let plain = read_input(args.input.as_deref());
    let mut token = encrypt(&plain, &args.passphrase).unwrap_or_else(|e| die(&e));
    token.push('\n');
    write_output(args.output.as_deref(), token.as_bytes());
}
