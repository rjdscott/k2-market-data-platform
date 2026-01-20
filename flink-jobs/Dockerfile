# Build stage - compile Java code to JAR
FROM maven:3.9.5-eclipse-temurin-11 AS builder

WORKDIR /build

# Copy POM and download dependencies (cached layer)
COPY pom.xml .
RUN mvn dependency:go-offline -B

# Copy source code and build
COPY src/ ./src/
RUN mvn clean package -DskipTests

# Runtime stage - minimal image with just the JAR
FROM flink-k2:1.18.1

USER root

# Copy the compiled JAR
COPY --from=builder /build/target/flink-bronze-jobs-1.0.0.jar /opt/flink/usrlib/

# Create submission script
COPY submit-job.sh /opt/flink/bin/submit-job.sh
RUN chmod +x /opt/flink/bin/submit-job.sh

USER flink

ENTRYPOINT ["/opt/flink/bin/submit-job.sh"]
