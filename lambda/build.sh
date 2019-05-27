#!/bin/sh

TS=$(date +"canvas-data-lambda-%m%d%Y-%H%M%S.zip")

echo "Building $TS"

mkdir package
cd package
echo "[install]\nprefix=" > setup.cfg
pip3 install -q --no-warn-conflicts -r ../requirements.txt --target .
zip -q -r9 ../$TS .
cd ..
rm -rf package
zip -q -9 $TS *.py
echo "Done."
