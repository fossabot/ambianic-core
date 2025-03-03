# exit bash script on error
set -e

# verbose mode
set -x

TAG="ambianic/ambianic-dev"

docker build --file Dev.Dockerfile --tag $TAG ./
docker run --rm --entrypoint echo "$TAG" "Hello $hello"
# docker push $TAG
