use txtcrypt::{decrypt, die, parse_args, read_input, write_output};

fn main() {
    let args = parse_args("decrypt", "encrypt 가 만든 암호문 텍스트를 원래 문자열로 되돌립니다.");
    let input = read_input(args.input.as_deref());
    let token = String::from_utf8_lossy(&input);
    let plain = decrypt(&token, &args.passphrase).unwrap_or_else(|e| die(&e));
    write_output(args.output.as_deref(), &plain);
}
