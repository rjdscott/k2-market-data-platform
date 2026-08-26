use rdkafka::config::ClientConfig;
use rdkafka::producer::FutureProducer;

fn main() {
    let (ver_n, ver_s) = rdkafka::util::get_rdkafka_version();
    println!("librdkafka {ver_s} (0x{ver_n:08x})");
    let p: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", "redpanda:9092")
        .set("compression.type", "zstd")
        .create()
        .expect("producer");
    println!("producer created: {}", std::any::type_name_of_val(&p));
}
