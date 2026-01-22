#!/bin/bash

# Start supervisor which manages both processes
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
