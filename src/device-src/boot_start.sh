[1][18:37:59] Not support std mode
#!/system/bin/sh
# AgentHub 开机自启动: memory_server(:8000) + agent sidecar(:8765)
# 由 /vendor/etc/init.pico.cfg 的 agentboot 服务调用;内容可随时改,无需动 cfg。
log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> /data/agents/boot.log; }

log "agentboot start"
unset PYTHONHOME PYTHONPATH
. /data/python312/env.sh

# 等网络/存储就绪(最多 30s)
i=0
while [ $i -lt 15 ]; do
  [ -d /data/agents ] && break
  sleep 2; i=$((i+1))
done

# memory_server(start.sh 是 exec 前台跑,必须 & 放后台)
# 怪癖规避(自验证循环): server 启动后 REST 检索恒空,延迟数分钟后才可被 warmup 修复
# (早期 warmup 会被 server 的延迟初始化覆盖)。循环: 每 45s 真实检索探针,空则重跑
# warmup(裸SQL读+索引init+命中search 三步配方),直到命中或 8 轮超时。
if ! netstat -tnl 2>/dev/null | grep -q ':8000 '; then
  log "starting memory_server"
  setsid /bin/sh /data/agents/memory/start.sh &
  i=0
  while [ $i -lt 30 ]; do
    sleep 2; i=$((i+1))
    if netstat -tnl 2>/dev/null | grep -q ':8000 '; then break; fi
  done
fi

# 检索恢复循环(后台,不阻塞 agent_server 启动)
(
  unset PYTHONHOME PYTHONPATH
  . /data/python312/env.sh
  j=0
  while [ $j -lt 8 ]; do
    j=$((j+1))
    sleep 45
    st=$(python3.12 /data/agents/memory/search_ok.py 2>/dev/null)
    case "$st" in
      OK*) log "memory search OK (round $j)"; exit 0 ;;
      *)  log "memory search $st, warmup (round $j)"
          python3.12 /data/agents/memory/warmup_index.py >> /data/agents/boot.log 2>&1 ;;
    esac
  done
  log "memory search still empty after 8 rounds (known quirk)"
) &


# agent sidecar
if ! netstat -tnl 2>/dev/null | grep -q ':8765 '; then
  log "starting agent_server"
  setsid /bin/sh /data/agents/start_server.sh
fi
log "agentboot done"
