# Custom Hermes sandbox image — extends the default terminal.docker_image
# with PDF text-extraction libraries baked in, so every container built
# from this image has them from the start, regardless of how many times
# a container gets removed and recreated (mount changes, troubleshooting
# resets, etc.). Installing into a running container's writable layer
# (docker exec -u root pip install ...) only persists for that specific
# container instance — this bakes it into the image layer instead, which
# every future container inherits automatically.
#
# Add more packages here as the project needs them — this is the place
# to accumulate sandbox dependencies over time rather than re-installing
# ad hoc into whatever container happens to be running.

FROM nikolaik/python-nodejs:python3.11-nodejs20

RUN pip install --no-cache-dir pypdf pdfplumber
