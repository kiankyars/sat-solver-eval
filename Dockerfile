FROM sat-eval-toolchain:v1 AS builder
COPY solver/ solver/
COPY vendor/ vendor/
COPY scripts/build_solver.sh scripts/run_solver.sh scripts/
RUN ./scripts/build_solver.sh

FROM sat-eval-toolchain:v1
COPY --from=builder /opt/sat/build /opt/sat/build
COPY --from=builder /opt/sat/scripts/run_solver.sh /opt/sat/scripts/run_solver.sh
USER 65534:65534
ENTRYPOINT []
CMD ["/opt/sat/scripts/run_solver.sh"]
