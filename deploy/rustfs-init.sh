#!/bin/sh
# One-shot RustFS setup (P6-12), safe to re-run: the document bucket, a policy limited to that bucket and
# the app user api signs with. Root credentials stay inside rustfs and this job; api never gets them.
set -eu
root="--aws-sigv4 aws:amz:us-east-1:s3 --user $RUSTFS_ACCESS_KEY:$RUSTFS_SECRET_KEY -fsS -o /dev/null"
admin=http://rustfs:9000/rustfs/admin/v3
policy='{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":["arn:aws:s3:::'"$BUCKET"'/*"]},
 {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":["arn:aws:s3:::'"$BUCKET"'"]}]}'
curl $root -X PUT "http://rustfs:9000/$BUCKET"
curl $root -X PUT -H 'Content-Type: application/json' --data "$policy" "$admin/add-canned-policy?name=kai-documents"
# Re-running with a new secret rotates the app user's key.
curl $root -X PUT -H 'Content-Type: application/json' \
  --data "{\"secretKey\":\"$APP_SECRET_KEY\",\"status\":\"enabled\"}" "$admin/add-user?accessKey=$APP_ACCESS_KEY"
curl $root -X PUT "$admin/set-user-or-group-policy?policyName=kai-documents&userOrGroup=$APP_ACCESS_KEY&isGroup=false"
