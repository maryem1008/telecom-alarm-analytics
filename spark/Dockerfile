FROM bitnami/spark:3.5

USER root
WORKDIR /app

COPY common/ common/
COPY spark/ spark/
COPY sql/ sql/

# The Postgres JDBC driver is resolved via spark.jars.packages in
# analytics_job.py's build_spark_session() (same code path as the native/
# no-Docker run), and cached under /opt/bitnami/spark's Ivy cache on first
# use — no separate jar download step here, so there's only one place that
# decides which driver version is used.

CMD ["spark-submit", "--master", "local[*]", "spark/analytics_job.py"]
