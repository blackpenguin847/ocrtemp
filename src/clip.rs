//! Windows 클립보드 텍스트 읽기/쓰기와 콘솔 창 유지. (외부 크레이트 없이 Win32 직접 호출)

#[cfg(windows)]
mod imp {
    use std::ffi::c_void;

    const CF_UNICODETEXT: u32 = 13;
    const GMEM_MOVEABLE: u32 = 2;

    #[link(name = "user32")]
    extern "system" {
        fn OpenClipboard(owner: *mut c_void) -> i32;
        fn CloseClipboard() -> i32;
        fn EmptyClipboard() -> i32;
        fn GetClipboardData(format: u32) -> *mut c_void;
        fn SetClipboardData(format: u32, mem: *mut c_void) -> *mut c_void;
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GlobalAlloc(flags: u32, bytes: usize) -> *mut c_void;
        fn GlobalFree(mem: *mut c_void) -> *mut c_void;
        fn GlobalLock(mem: *mut c_void) -> *mut c_void;
        fn GlobalUnlock(mem: *mut c_void) -> i32;
        fn GlobalSize(mem: *mut c_void) -> usize;
        fn GetConsoleProcessList(list: *mut u32, count: u32) -> u32;
    }

    pub const SUPPORTED: bool = true;

    /// 다른 프로그램이 클립보드를 잠깐 잡고 있을 수 있어 몇 번 재시도한다.
    fn open() -> Result<(), String> {
        for _ in 0..20 {
            if unsafe { OpenClipboard(std::ptr::null_mut()) } != 0 {
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        Err("클립보드를 열 수 없습니다 (다른 프로그램이 사용 중)".into())
    }

    pub fn get_text() -> Result<String, String> {
        open()?;
        let text = unsafe {
            let h = GetClipboardData(CF_UNICODETEXT);
            let p = if h.is_null() { h } else { GlobalLock(h) } as *const u16;
            let text = if p.is_null() {
                None
            } else {
                let max = GlobalSize(h) / 2;
                let mut len = 0;
                while len < max && *p.add(len) != 0 {
                    len += 1;
                }
                let s = String::from_utf16_lossy(std::slice::from_raw_parts(p, len));
                GlobalUnlock(h);
                Some(s)
            };
            CloseClipboard();
            text
        };
        text.ok_or_else(|| "클립보드에 텍스트가 없습니다".to_string())
    }

    pub fn set_text(text: &str) -> Result<(), String> {
        let wide: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
        open()?;
        let ok = unsafe {
            EmptyClipboard();
            let h = GlobalAlloc(GMEM_MOVEABLE, wide.len() * 2);
            let p = if h.is_null() { h } else { GlobalLock(h) } as *mut u16;
            let ok = if p.is_null() {
                false
            } else {
                std::ptr::copy_nonoverlapping(wide.as_ptr(), p, wide.len());
                GlobalUnlock(h);
                // 성공하면 메모리 소유권이 시스템으로 넘어간다.
                !SetClipboardData(CF_UNICODETEXT, h).is_null()
            };
            if !ok && !h.is_null() {
                GlobalFree(h);
            }
            CloseClipboard();
            ok
        };
        if ok { Ok(()) } else { Err("클립보드에 쓸 수 없습니다".into()) }
    }

    /// 더블클릭·드래그로 실행되어 이 프로세스만 콘솔을 쓰고 있는지.
    pub fn owns_console() -> bool {
        let mut list = [0u32; 2];
        unsafe { GetConsoleProcessList(list.as_mut_ptr(), 2) == 1 }
    }
}

#[cfg(not(windows))]
mod imp {
    pub const SUPPORTED: bool = false;
    const MSG: &str = "클립보드는 Windows에서만 지원합니다";
    pub fn get_text() -> Result<String, String> {
        Err(MSG.into())
    }
    pub fn set_text(_: &str) -> Result<(), String> {
        Err(MSG.into())
    }
    pub fn owns_console() -> bool {
        false
    }
}

pub use imp::*;
