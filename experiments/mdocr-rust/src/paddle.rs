use std::io::Cursor;
use std::sync::LazyLock;
use std::thread::sleep;
use std::time::{Duration, Instant};

use image::{DynamicImage, ImageDecoder, ImageReader, Limits, codecs::jpeg::JpegEncoder};
use regex::{Captures, Regex};
use reqwest::{
    Url,
    blocking::{Client, Response, multipart},
    header::{AUTHORIZATION, HeaderValue},
};
use serde_json::{Value, json};

use crate::provider::Failure;

pub(crate) const JOB_URL: &str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs";
pub(crate) const MODEL: &str = "PaddleOCR-VL-1.6";
pub(crate) const JPEG_QUALITY: u8 = 90;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const POLL_INTERVAL: Duration = Duration::from_secs(5);
const JOB_TIMEOUT: Duration = Duration::from_secs(900);
const GET_RETRIES: u32 = 3;

pub(crate) struct Prepared(Vec<u8>);

pub(crate) fn prepare(original: &[u8]) -> anyhow::Result<Prepared> {
    let mut decoder = ImageReader::new(Cursor::new(original))
        .with_guessed_format()?
        .into_decoder()?;
    let mut limits = Limits::default();
    limits.reserve(decoder.total_bytes())?;
    decoder.set_limits(limits)?;
    let orientation = decoder.orientation()?;
    let mut image = DynamicImage::from_decoder(decoder)?;
    image.apply_orientation(orientation);

    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, JPEG_QUALITY).encode_image(&image.into_rgb8())?;
    Ok(Prepared(jpeg))
}

pub(crate) fn convert(
    http: &Client,
    endpoint: Option<&str>,
    token: &str,
    image: Prepared,
) -> Result<String, Failure> {
    let endpoint = http_url(endpoint.unwrap_or(JOB_URL).trim_end_matches('/'))
        .ok_or_else(|| Failure::known("OCR_PROVIDER_FAILED"))?;
    let mut auth = HeaderValue::from_str(&format!("bearer {token}"))
        .map_err(|_| Failure::known("OCR_AUTH_UNAVAILABLE"))?;
    auth.set_sensitive(true);
    let optional_payload = json!({
        "useDocOrientationClassify": false,
        "useDocUnwarping": false,
        "useChartRecognition": false,
    });
    let file = multipart::Part::bytes(image.0)
        .file_name("prepared.jpg")
        .mime_str("image/jpeg")
        .map_err(|_| Failure::known("OCR_PROVIDER_FAILED"))?;
    let form = multipart::Form::new()
        .text("model", MODEL)
        .text("optionalPayload", optional_payload.to_string())
        .part("file", file);

    // An interrupted submission may already have created a billable job.
    let request = http
        .post(endpoint.clone())
        .timeout(REQUEST_TIMEOUT)
        .header(AUTHORIZATION, auth.clone())
        .multipart(form)
        .build()
        .map_err(|_| Failure::known("OCR_PROVIDER_FAILED"))?;
    let response = http
        .execute(request)
        .map_err(|_| Failure::unknown("OCR_PROVIDER_FAILED"))?;
    if !response.status().is_success() {
        let status = response.status().as_u16();
        return Err(Failure::http(status, !(400..500).contains(&status)));
    }
    let submission: Value = response
        .json()
        .map_err(|_| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
    let job_id = submission["data"]["jobId"]
        .as_str()
        .filter(|id| !id.is_empty())
        .filter(|id| !matches!(*id, "." | ".."))
        .ok_or_else(|| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
    let mut job_url = endpoint;
    job_url
        .path_segments_mut()
        .map_err(|_| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?
        .push(job_id);
    poll(http, job_url, &auth)
}

fn http_url(input: &str) -> Option<Url> {
    let url = Url::parse(input).ok()?;
    (matches!(url.scheme(), "http" | "https")
        && url.host().is_some()
        && url.username().is_empty()
        && url.password().is_none())
    .then_some(url)
}

fn get(http: &Client, url: &Url, auth: Option<&HeaderValue>) -> Result<Response, Failure> {
    for attempt in 0..=GET_RETRIES {
        let mut request = http.get(url.clone()).timeout(REQUEST_TIMEOUT);
        if let Some(auth) = auth {
            request = request.header(AUTHORIZATION, auth.clone());
        }
        let failure = match request.send() {
            Ok(response) if response.status().is_success() => return Ok(response),
            Ok(response) => {
                let status = response.status().as_u16();
                let failure = Failure::http(status, true);
                if !matches!(status, 500 | 502 | 503 | 504) {
                    return Err(failure);
                }
                failure
            }
            Err(_) => Failure::unknown("OCR_PROVIDER_FAILED"),
        };
        if attempt == GET_RETRIES {
            return Err(failure);
        }
        sleep(Duration::from_secs(2 << attempt));
    }
    unreachable!()
}

fn poll(http: &Client, job_url: Url, auth: &HeaderValue) -> Result<String, Failure> {
    let deadline = Instant::now() + JOB_TIMEOUT;
    while Instant::now() < deadline {
        let job: Value = get(http, &job_url, Some(auth))?
            .json()
            .map_err(|_| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
        let data = &job["data"];
        match data["state"].as_str() {
            Some("done") => {
                let result_url = data["resultUrl"]["jsonUrl"]
                    .as_str()
                    .and_then(http_url)
                    .ok_or_else(|| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
                // The signed result URL is independent of job API authentication.
                let result = get(http, &result_url, None)?
                    .bytes()
                    .map_err(|_| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
                let result = std::str::from_utf8(&result)
                    .map_err(|_| Failure::unknown("OCR_OUTCOME_UNKNOWN"))?;
                return parse_jsonl(result)
                    .map(|markdown| text_only(&markdown))
                    .ok_or_else(|| Failure::unknown("OCR_OUTCOME_UNKNOWN"));
            }
            Some("failed") => return Err(Failure::known("OCR_PROVIDER_FAILED")),
            Some("pending" | "running") => sleep(POLL_INTERVAL),
            _ => return Err(Failure::unknown("OCR_OUTCOME_UNKNOWN")),
        }
    }
    Err(Failure::unknown("OCR_OUTCOME_UNKNOWN"))
}

fn parse_jsonl(input: &str) -> Option<String> {
    let mut blocks = Vec::new();
    for line in input.lines().filter(|line| !line.trim().is_empty()) {
        let value: Value = serde_json::from_str(line).ok()?;
        let result = value.get("result").unwrap_or(&value);
        let pages = result.get("layoutParsingResults")?.as_array()?;
        let mut page_blocks = Vec::new();
        for page in pages {
            if let Some(text) = page["markdown"].get("text").filter(|text| !text.is_null()) {
                let text = text.as_str()?.trim();
                if !text.is_empty() {
                    page_blocks.push(text.to_owned());
                    continue;
                }
            }
            if let Some(parsing) = page["prunedResult"].get("parsing_res_list") {
                let mut fallback = Vec::new();
                for item in parsing.as_array()? {
                    if let Some(content) = item.get("block_content") {
                        let text = content.as_str()?.trim();
                        if !text.is_empty() {
                            fallback.push(text);
                        }
                    }
                }
                if !fallback.is_empty() {
                    page_blocks.push(fallback.join("\n\n"));
                }
            }
        }
        if page_blocks.is_empty() {
            return None;
        }
        blocks.extend(page_blocks);
    }
    (!blocks.is_empty()).then(|| blocks.join("\n\n"))
}

fn text_only(markdown: &str) -> String {
    static MARKDOWN_IMAGE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"!\[([^\]]*)\]\((?:<[^>]+>|[^\s)]+)(?:\s+['"][^'"]*['"])?\)"#)
            .expect("fixed Markdown image pattern")
    });
    static HTML_IMAGE: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)<img\b[^>]*>").expect("fixed HTML image pattern"));

    let markdown = MARKDOWN_IMAGE.replace_all(markdown, |m: &Captures<'_>| {
        let label = m[1].trim();
        let label = if label.is_empty() {
            "untranscribed"
        } else {
            label
        };
        format!("[PaddleOCR image region: {label}]")
    });
    HTML_IMAGE
        .replace_all(&markdown, "[PaddleOCR image region: untranscribed]")
        .trim()
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::ImageEncoder;

    #[test]
    fn parses_layout_fallback_and_removes_provider_image_embeds() {
        let input = r#"{"result":{"layoutParsingResults":[{"markdown":{"text":"  caption ![chart](generated.png) "}},{"markdown":{"text":""},"prunedResult":{"parsing_res_list":[{"block_content":" one "},{"block_content":"two <img src='generated.jpg'>"}]}}]}}"#;
        assert_eq!(
            text_only(&parse_jsonl(input).unwrap()),
            "caption [PaddleOCR image region: chart]\n\none\n\ntwo [PaddleOCR image region: untranscribed]"
        );
        assert!(parse_jsonl(&format!("{input}\n{{}}")).is_none());
        assert!(parse_jsonl(&input.replace("\" one \"", "17")).is_none());
    }

    #[test]
    fn prepares_jpeg_and_rejects_unreadable_data() {
        let image = DynamicImage::new_rgba8(11, 7);
        let mut png = Cursor::new(Vec::new());
        image.write_to(&mut png, image::ImageFormat::Png).unwrap();
        let jpeg = prepare(png.get_ref()).unwrap().0;
        assert_eq!(
            image::guess_format(&jpeg).unwrap(),
            image::ImageFormat::Jpeg
        );
        let decoded = image::load_from_memory(&jpeg).unwrap();
        assert_eq!((decoded.width(), decoded.height()), (11, 7));
        assert!(prepare(b"not an image").is_err());
    }

    #[test]
    fn applies_exif_orientation_to_jpeg_pixels() {
        let mut original = Vec::new();
        let mut encoder = JpegEncoder::new_with_quality(&mut original, JPEG_QUALITY);
        // TIFF IFD0 with one SHORT orientation entry set to 6 (rotate 90°).
        let orientation = b"II\x2a\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00\x00\x00\x00\x00";
        encoder.set_exif_metadata(orientation.to_vec()).unwrap();
        encoder
            .encode_image(&DynamicImage::new_rgb8(11, 7))
            .unwrap();

        let normalized = prepare(&original).unwrap().0;
        let image = image::load_from_memory(&normalized).unwrap();
        assert_eq!((image.width(), image.height()), (7, 11));
    }
}
