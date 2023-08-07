FROM nixos/nix

RUN nix-channel --add https://channels.nixos.org/nixos-23.05 nixpkgs && \
    nix-channel --update

WORKDIR /app

ENV DOCKER=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIPENV_CLEAR=1

COPY Pipfile.lock shell.nix ./
RUN nix-shell --run "true"

COPY LICENSE Makefile *.py ./
COPY api ./api/
COPY middlewares ./middlewares/
COPY models ./models/
COPY states ./states/

RUN nix-shell --run "make version"

EXPOSE 8000
ENTRYPOINT [ "nix-shell", "--run" ]
CMD [ "pipenv run uvicorn main:app --host 0.0.0.0" ]
