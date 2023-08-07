#!/bin/sh
set -e

echo "Initializing MongoDB replica set..."
sleep 1

# wait for MongoDB to start
until mongosh --host db --eval 'db.runCommand({ ping: 1 })'
do
    echo "$(date) - Waiting for MongoDB to start"
    sleep 1
done

mongosh --host db --eval "rs.initiate({_id: 'rs0', members:[{_id: 0, host:'$1'}]})"

echo "Replica set initiated"
