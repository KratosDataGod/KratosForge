use std::thread;
use std::time::Duration;

use reqwest::blocking::Client;
use serde_json::Value;

const TRANSIENT: &[u16] = &[408, 429, 500, 502, 503, 504];

pub fn http_get_json(token: &str, url: &str, query: &[(&str, &str)]) -> Result<Value, String> {
    let client = Client::new();
    let mut last_err = String::new();
    for attempt in 0..5 {
        let resp = client
            .get(url)
            .header("Authorization", format!("Bearer {token}"))
            .query(query)
            .send();
        match resp {
            Ok(r) if r.status().is_success() => {
                return r.json().map_err(|e| e.to_string());
            }
            Ok(r) => {
                let status = r.status().as_u16();
                last_err = format!("HTTP {status}: {}", r.text().unwrap_or_default());
                if TRANSIENT.contains(&status) && attempt < 4 {
                    thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                    continue;
                }
                return Err(last_err);
            }
            Err(e) => {
                last_err = e.to_string();
                if attempt < 4 {
                    thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                    continue;
                }
            }
        }
    }
    Err(last_err)
}
