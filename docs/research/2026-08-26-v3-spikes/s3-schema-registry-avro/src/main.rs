use apache_avro::types::Value;
use schema_registry_converter::async_impl::easy_avro::EasyAvroEncoder;
use schema_registry_converter::async_impl::schema_registry::SrSettings;
use schema_registry_converter::schema_registry_common::SubjectNameStrategy;

#[tokio::main]
async fn main() {
    let sr = SrSettings::new("http://fake-registry:8081".to_string());
    let encoder = EasyAvroEncoder::new(sr);
    let strategy = SubjectNameStrategy::TopicNameStrategy("market.crypto.trades.kraken".to_string(), false);
    let record = vec![("price", Value::Long(4528520000000i64))];
    match encoder.encode(record, strategy).await {
        Ok(bytes) => println!("encoded {} bytes", bytes.len()),
        Err(e) => println!("expected failure vs fake registry: {e}"),
    }
}
