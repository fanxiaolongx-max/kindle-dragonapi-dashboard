#!/bin/sh
DIR=/mnt/us/extensions/dragonapi-dashboard
PID="$DIR/dashboard.pid"
LOG="$DIR/dashboard.log"
STATE="$DIR/display-state"
case "$1" in
 start)
  if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then exit 0; fi
  if [ ! -f "$STATE" ]; then
   ORIENTATION=$(lipc-get-prop com.lab126.winmgr orientationLock 2>/dev/null)
   SLEEP=$(lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null)
   printf '%s\n%s\n' "$ORIENTATION" "$SLEEP" > "$STATE"
  fi
  lipc-set-prop -s com.lab126.winmgr orientationLock L
  lipc-set-prop -i com.lab126.powerd preventScreenSaver 1
  python3 "$DIR/bin/dashboard.py" >> "$LOG" 2>&1 &
  echo $! > "$PID"
  ;;
 stop)
  if [ -f "$PID" ]; then kill "$(cat "$PID")" 2>/dev/null; rm -f "$PID"; fi
  if [ -f "$STATE" ]; then
   ORIENTATION=$(sed -n '1p' "$STATE")
   SLEEP=$(sed -n '2p' "$STATE")
   case "$ORIENTATION" in U|D|L|R) lipc-set-prop -s com.lab126.winmgr orientationLock "$ORIENTATION" ;; esac
   case "$SLEEP" in 0|1) lipc-set-prop -i com.lab126.powerd preventScreenSaver "$SLEEP" ;; esac
   rm -f "$STATE"
  fi
  ;;
 once) python3 "$DIR/bin/dashboard.py" --once >> "$LOG" 2>&1 ;;
 *) exit 1 ;;
esac
