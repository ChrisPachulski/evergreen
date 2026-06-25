// evergreen-rust-cov — doc-comment coverage for Rust, via the `syn` parser (the de-facto
// Rust syntactic parser, what every proc-macro uses). Prints "<documented> <total>" for
// public items across all file args. Pure syntactic parse: unresolved `mod foo;`/`use`
// don't matter (this is why standalone `rustdoc` is unusable — it resolves modules).
// Files that don't parse are skipped so the rest still count. Built/cached by
// bin/evergreen-scan on first --coverage run; falls back to regex if cargo is absent.
use std::fs;
use syn::{ImplItem, Item, Visibility};

fn is_pub(v: &Visibility) -> bool {
    matches!(v, Visibility::Public(_))
}
// A doc comment (`///`, `//!`, or `#[doc = ...]`) desugars to a `doc` attribute.
fn has_doc(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| a.path().is_ident("doc"))
}

fn main() {
    let (mut doc, mut tot) = (0u64, 0u64);
    for path in std::env::args().skip(1) {
        let src = match fs::read_to_string(&path) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let file = match syn::parse_file(&src) {
            Ok(f) => f,
            Err(_) => continue,
        };
        for item in &file.items {
            // pub fn/struct/enum/trait/type at the top level (parity with the regex),
            // plus pub methods inside impl blocks.
            let (is_p, attrs) = match item {
                Item::Fn(i) => (is_pub(&i.vis), &i.attrs),
                Item::Struct(i) => (is_pub(&i.vis), &i.attrs),
                Item::Enum(i) => (is_pub(&i.vis), &i.attrs),
                Item::Trait(i) => (is_pub(&i.vis), &i.attrs),
                Item::Type(i) => (is_pub(&i.vis), &i.attrs),
                Item::Impl(im) => {
                    for ii in &im.items {
                        if let ImplItem::Fn(m) = ii {
                            if is_pub(&m.vis) {
                                tot += 1;
                                if has_doc(&m.attrs) {
                                    doc += 1;
                                }
                            }
                        }
                    }
                    continue;
                }
                _ => continue,
            };
            if is_p {
                tot += 1;
                if has_doc(attrs) {
                    doc += 1;
                }
            }
        }
    }
    println!("{} {}", doc, tot);
}
