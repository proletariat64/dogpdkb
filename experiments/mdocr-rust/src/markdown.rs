use crate::artifacts::sha;
use html5ever::tendril::StrTendril;
use html5ever::tokenizer::{
    BufferQueue, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer, states::RawKind,
};
use percent_encoding::percent_decode_str;
use pulldown_cmark::{Event, LinkType, Parser, Tag, TagEnd};
use reqwest::Url;
use std::cell::RefCell;
use std::ops::Range;
use std::path::PathBuf;

pub(crate) struct Occurrence {
    pub target: String,
    pub span: Range<usize>,
    pub edit: Range<usize>,
}

#[derive(Debug)]
pub(crate) enum Destination {
    Local(PathBuf),
    Remote(Url),
    Inline,
    Unsupported,
}

pub(crate) fn destination(target: &str) -> Destination {
    if target
        .get(..5)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("data:"))
    {
        return Destination::Inline;
    }
    if let Ok(url) = Url::parse(target) {
        if matches!(url.scheme(), "http" | "https")
            && url.username().is_empty()
            && url.password().is_none()
        {
            return Destination::Remote(url);
        }
        return Destination::Unsupported;
    }
    let path = target.split_once('#').map_or(target, |(path, _)| path);
    let Ok(path) = percent_decode_str(path).decode_utf8() else {
        return Destination::Unsupported;
    };
    if path.is_empty() || path.starts_with(['/', '\\']) {
        return Destination::Unsupported;
    }
    let path = PathBuf::from(path.as_ref());
    if path.is_absolute() {
        return Destination::Unsupported;
    }
    Destination::Local(path)
}

impl Occurrence {
    pub fn resource_id(&self) -> String {
        match destination(&self.target) {
            Destination::Inline => format!("data:sha256:{}", sha(self.target.as_bytes())),
            Destination::Local(path) => path.to_string_lossy().into_owned(),
            Destination::Remote(_) => self.target.clone(),
            Destination::Unsupported => {
                format!("unsupported:sha256:{}", sha(self.target.as_bytes()))
            }
        }
    }
}

#[derive(Default)]
struct HtmlImages(RefCell<Vec<String>>);

impl TokenSink for HtmlImages {
    type Handle = ();

    fn process_token(&self, token: Token, _: u64) -> TokenSinkResult<()> {
        if let Token::TagToken(tag) = token
            && tag.kind == TagKind::StartTag
        {
            match tag.name.as_ref() {
                "img" => {
                    if let Some(src) = tag
                        .attrs
                        .iter()
                        .find(|attribute| &*attribute.name.local == "src")
                    {
                        self.0.borrow_mut().push(src.value.to_string());
                    }
                }
                "script" => return TokenSinkResult::RawData(RawKind::ScriptData),
                "style" | "xmp" | "iframe" | "noembed" | "noframes" => {
                    return TokenSinkResult::RawData(RawKind::Rawtext);
                }
                "title" | "textarea" => return TokenSinkResult::RawData(RawKind::Rcdata),
                "plaintext" => return TokenSinkResult::Plaintext,
                _ => {}
            }
        }
        TokenSinkResult::Continue
    }
}

fn html_images(source: &str) -> Vec<String> {
    let input = BufferQueue::default();
    input.push_back(StrTendril::from(source));
    let tokenizer = Tokenizer::new(HtmlImages::default(), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer.sink.0.into_inner()
}

pub(crate) fn occurrences(text: &str) -> Vec<Occurrence> {
    let mut generated = Vec::new();
    let mut generated_start = None;
    let mut output = Vec::new();
    let mut image_depth = 0;
    let mut link_ends = Vec::new();
    let mut blocks = Vec::new();
    let mut containers = Vec::new();
    let mut html_block = false;
    for (event, mut span) in Parser::new(text).into_offset_iter() {
        match event {
            Event::Start(Tag::Paragraph) => blocks.push((span, true)),
            Event::Start(Tag::Heading { .. }) => blocks.push((span, false)),
            Event::End(TagEnd::Paragraph | TagEnd::Heading(_)) => {
                blocks.pop();
            }
            Event::Start(
                Tag::List(_) | Tag::BlockQuote(_) | Tag::FootnoteDefinition(_) | Tag::Table(_),
            ) => {
                containers.push(span.end);
            }
            Event::End(
                TagEnd::List(_)
                | TagEnd::BlockQuote(_)
                | TagEnd::FootnoteDefinition
                | TagEnd::Table,
            ) => {
                containers.pop();
            }
            Event::Start(Tag::Link { .. }) => link_ends.push(span.end),
            Event::End(TagEnd::Link) => {
                link_ends.pop();
            }
            Event::Start(Tag::Image {
                link_type,
                dest_url,
                ..
            }) => {
                image_depth += 1;
                if image_depth > 1 {
                    continue;
                }
                // pulldown-cmark's collapsed-reference range omits the second
                // bracket pair. The event identifies its type unambiguously.
                if link_type == LinkType::Collapsed && text[span.end..].starts_with("[]") {
                    span.end += 2;
                }
                let edit = if let Some(&end) = containers.first() {
                    // Keep list and quote continuations attached to their block.
                    end..end
                } else if let Some((block, paragraph)) = blocks.last() {
                    if *paragraph
                        && link_ends.is_empty()
                        && text[block.clone()].trim() == &text[span.clone()]
                    {
                        span.clone()
                    } else {
                        block.end..block.end
                    }
                } else if let Some(&end) = link_ends.first() {
                    end..end
                } else {
                    span.clone()
                };
                output.push(Occurrence {
                    target: dest_url.into_string(),
                    span,
                    edit,
                });
            }
            Event::End(TagEnd::Image) => image_depth -= 1,
            Event::Start(Tag::HtmlBlock) => {
                html_block = true;
                let end = containers.first().copied().unwrap_or(span.end);
                add_html(text, &mut output, span, end);
            }
            Event::End(TagEnd::HtmlBlock) => html_block = false,
            Event::InlineHtml(html) | Event::Html(html) => {
                let html = html.trim();
                if generated_start.is_none()
                    && html.starts_with("<!-- mdocr:ocr-begin ")
                    && html.ends_with("-->")
                {
                    generated_start = Some(span.start);
                } else if html == "<!-- mdocr:ocr-end -->"
                    && let Some(open) = generated_start.take()
                {
                    generated.push(open..span.end);
                }
                if !html_block && image_depth == 0 {
                    let end = containers
                        .first()
                        .copied()
                        .or_else(|| blocks.last().map(|(block, _)| block.end))
                        .unwrap_or(span.end);
                    add_html(text, &mut output, span, end);
                }
            }
            _ => {}
        }
    }
    output.retain(|occurrence| !in_generated(&generated, occurrence.span.start));
    output
}

fn in_generated(ranges: &[Range<usize>], start: usize) -> bool {
    ranges
        .get(ranges.partition_point(|range| range.end <= start))
        .is_some_and(|range| range.contains(&start))
}

fn add_html(text: &str, output: &mut Vec<Occurrence>, span: Range<usize>, end: usize) {
    output.extend(
        html_images(&text[span.clone()])
            .into_iter()
            .map(|target| Occurrence {
                target,
                span: span.clone(),
                edit: end..end,
            }),
    );
}
