FROM alpine:latest

WORKDIR /opt/phabfive
COPY setup.py /opt/phabfive/setup.py
COPY *.md /opt/phabfive/
COPY phabfive/ /opt/phabfive/phabfive/

RUN apk update
RUN apk add python3 py3-pip
RUN pip3 install -e .

ENTRYPOINT ["/usr/bin/phabfive"]
CMD ["$1"]
