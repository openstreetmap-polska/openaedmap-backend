#!/bin/bash

# Try to apply database migrations
echo "Apply database migrations"
until alembic upgrade head ; do
    sleep 3
    echo "Waiting for database...Retry!";
done

# Start server
echo "Starting server"
uvicorn backend.main:app $( (( $DEV == 1 )) && printf %s '--reload' ) --host 0.0.0.0 --port 8000
