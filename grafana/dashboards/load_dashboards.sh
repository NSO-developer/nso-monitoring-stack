#!/bin/sh

curl -H "Content-Type: application/json" -X POST http://localhost:3000/api/folders --upload-file folder.json
curl -H "Content-Type: application/json" -X POST http://localhost:3000/api/dashboards/db --upload-file log_files.json
