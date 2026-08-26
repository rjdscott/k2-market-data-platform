plugins {
    kotlin("jvm") version "2.3.10"
    kotlin("plugin.serialization") version "2.3.10"
    application
}

group = "com.k2"
version = "1.0.0-SNAPSHOT"

repositories {
    mavenCentral()
    maven("https://packages.confluent.io/maven/")
}

dependencies {
    // Kotlin
    implementation("org.jetbrains.kotlin:kotlin-stdlib")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.0")

    // Ktor Client (WebSocket)
    implementation("io.ktor:ktor-client-core:3.1.0")
    implementation("io.ktor:ktor-client-cio:3.1.0")
    implementation("io.ktor:ktor-client-websockets:3.1.0")
    implementation("io.ktor:ktor-client-logging:3.1.0")

    // Kafka & Avro
    implementation("org.apache.kafka:kafka-clients:4.1.0")
    implementation("io.confluent:kafka-avro-serializer:7.8.2")
    implementation("org.apache.avro:avro:1.12.0")

    // Logging
    implementation("io.github.oshai:kotlin-logging-jvm:7.0.3")
    implementation("ch.qos.logback:logback-classic:1.5.16")

    // Configuration
    implementation("com.typesafe:config:1.4.3")

    // YAML config support (for instruments.yaml)
    implementation("com.charleskorn.kaml:kaml:0.67.0")

    // Metrics — Micrometer Prometheus registry + Ktor HTTP server for /metrics endpoint
    implementation("io.micrometer:micrometer-registry-prometheus:1.14.5")
    implementation("io.ktor:ktor-server-core:3.1.0")
    implementation("io.ktor:ktor-server-netty:3.1.0")

    // Testing
    testImplementation(kotlin("test"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.1")
}

application {
    mainClass.set("com.k2.feedhandler.MainKt")
}

kotlin {
    jvmToolchain(21)
}

tasks.test {
    useJUnitPlatform()
}

// Fat JAR for deployment
tasks.register<Jar>("fatJar") {
    archiveClassifier.set("all")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE

    manifest {
        attributes["Main-Class"] = "com.k2.feedhandler.MainKt"
    }

    from(configurations.runtimeClasspath.get().map { if (it.isDirectory) it else zipTree(it) })
    with(tasks.jar.get())
}
