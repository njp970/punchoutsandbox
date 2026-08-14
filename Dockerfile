# PunchOut Sandbox — the Lambda image.
#
# The base is AWS's own arm64 Python 3.12 Lambda image, matching Xenia's
# arm64 posture (cheaper per ms, and the same architecture the team already
# builds wheels for).
#
# WHY THIS IS A CONTAINER AND NOT A ZIP: see infra/sandbox/site_stack.py's
# module docstring. Short version — `lxml` is the only sane Python DTD
# validator, DTD validation is the entire product, and `lxml` in a zip bundle
# is the reason Xenia never got it.
FROM public.ecr.aws/lambda/python:3.12-arm64

# gcc/libxml2-devel are needed only if pip has to build lxml from source.
# It normally installs a manylinux aarch64 wheel and these go unused, but a
# wheel-less lxml release has happened before and a build that fails only on
# CI at 2am is not worth the 40MB saved.
RUN dnf install -y gcc libxml2-devel libxslt-devel && dnf clean all

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# The DTDs are vendored into the repo rather than fetched here: a docker build
# that reaches out to xml.cxml.org is a build that fails when someone else's
# webserver is down, and a validator whose rules can change under you without
# a commit is not a validator anyone should trust a conformance verdict from.
# See app/cxml/dtd/README.md and scripts/fetch_dtds.sh.
COPY app/ ${LAMBDA_TASK_ROOT}/app/

CMD ["app.handler.handler"]
