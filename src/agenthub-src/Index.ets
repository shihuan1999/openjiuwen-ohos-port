import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';

interface ToolEvent {
  type: string;
  tool?: string;
  args?: string;
  preview?: string;
  detail?: string;
  ms?: number;
  cmd?: string;
  rc?: number;
  head?: string;
  iter?: number;
  text?: string;
  plan?: string;
}

interface TaskInfo {
  id: string;
  agent: string;
  state: string;
  events: ToolEvent[];
  result?: string;
  error?: string;
  elapsed?: number;
}

interface RunResp {
  id?: string;
  error?: string;
}

interface RecentTasks {
  diag?: string;
  perf?: string;
  kb?: string;
}

interface HealthResp {
  ok?: boolean;
  model?: string;
  agents?: string[];
  running?: string;
  running_kind?: string;
  recent?: RecentTasks;
}

const C_BG = '#0e1116';
const C_CARD = '#171c24';
const C_CARD2 = '#10141b';
const C_ACCENT = '#3d8bff';
const C_OK = '#34c77b';
const C_WARN = '#ffb020';
const C_ERR = '#ff5d5d';
const C_THINK = '#a78bfa';
const C_TXT = '#e8ecf1';
const C_SUB = '#8b95a5';

const DEFAULT_DIAG_QUERY: string =
  '请对这台 OpenHarmony 设备做一次深入的应用健康诊断：' +
  '1) list_faultlogs 查看故障日志，若 7 天内有日志用 read_faultlog 读最新一条并总结；' +
  '2) hisysevent_recent(etype="FAULT") 看系统级故障事件；' +
  '3) get_param(key="const.product.software.version") 获取版本；' +
  '4) 若怀疑内核问题（DRM/VSync/OOM 等）用 dmesg_tail 或 diag_shell 深入验证；' +
  '5) 中文输出结构化诊断报告：版本、问题清单及证据、根因分析、建议。';

const DEFAULT_PERF_QUERY: string =
  '请对这台 OpenHarmony 设备做一次深入的性能体检：' +
  '1) sp_cpu 看整机 CPU 频率与占用；2) cpu_top_processes(n=5) 找最耗 CPU 进程；' +
  '3) mem_overview 与 mem_pressure 评估内存与回收压力；4) device_sensors 看 GPU 与温度；' +
  '5) 若发现异常用 klog_tail 或 perf_shell 深入内核定位；' +
  '6) 中文输出体检报告：指标、Top 进程、异常项（含内核证据）、优化建议。';

const DEFAULT_KB_QUERY: string =
  '请作为鸿蒙知识库助手回答：K3 Pico-ITX 的芯片规格与 AI 算力是多少？' +
  '用 kb_search 检索知识库并注明来源文件，用 device_probe(topic="thermal") 看本机实时温度，' +
  '并用 ltm_search 查一下有没有关于我的历史记忆，最后用中文输出结构化回答。';

@Entry
@Component
struct Index {
  @State serverUrl: string = 'http://127.0.0.1:8765';
  @State healthText: string = '未连接';
  @State healthy: boolean = false;
  @State modelName: string = '';
  @State diagQuery: string = DEFAULT_DIAG_QUERY;
  @State perfQuery: string = DEFAULT_PERF_QUERY;
  @State kbQuery: string = DEFAULT_KB_QUERY;
  @State stepsDiag: ToolEvent[] = [];
  @State stepsPerf: ToolEvent[] = [];
  @State stepsKb: ToolEvent[] = [];
  @State thinkDiag: ToolEvent[] = [];
  @State thinkPerf: ToolEvent[] = [];
  @State thinkKb: ToolEvent[] = [];
  @State diagResult: string = '';
  @State perfResult: string = '';
  @State kbResult: string = '';
  @State diagRunning: boolean = false;
  @State perfRunning: boolean = false;
  @State kbRunning: boolean = false;
  @State diagDone: boolean = false;
  @State perfDone: boolean = false;
  @State kbDone: boolean = false;
  @State tick: number = 0;
  private diagTaskId: string = '';
  private perfTaskId: string = '';
  private kbTaskId: string = '';
  private diagStartAt: number = 0;
  private perfStartAt: number = 0;
  private kbStartAt: number = 0;
  private consumedDiag: string[] = [];
  private consumedPerf: string[] = [];
  private consumedKb: string[] = [];
  private timerId: number = -1;

  aboutToAppear(): void {
    this.checkHealth();
    this.timerId = setInterval(() => {
      this.tick++;
      this.pollKind('diag');
      this.pollKind('perf');
      this.pollKind('kb');
      this.adoptRunning();
    }, 2000);
  }

  aboutToDisappear(): void {
    if (this.timerId >= 0) {
      clearInterval(this.timerId);
      this.timerId = -1;
    }
  }

  private requestJson(method: string, path: string, body: string,
      cb: (resp: string, ok: boolean) => void): void {
    const cli = http.createHttp();
    const opt: http.HttpRequestOptions = {
      method: method === 'POST' ? http.RequestMethod.POST : http.RequestMethod.GET,
      header: { 'Content-Type': 'application/json' },
      expectDataType: http.HttpDataType.STRING,
      connectTimeout: 8000,
      readTimeout: 10000
    };
    if (body !== '') {
      opt.extraData = body;
    }
    cli.request(this.serverUrl + path, opt).then((resp: http.HttpResponse) => {
      const code = resp.responseCode ?? 0;
      const text = typeof resp.result === 'string' ? resp.result : JSON.stringify(resp.result);
      cb(text, code >= 200 && code < 300);
      cli.destroy();
    }).catch((err: BusinessError) => {
      cb(JSON.stringify({ error: 'HTTP fail: ' + err.message }), false);
      cli.destroy();
    });
  }

  checkHealth(): void {
    this.healthText = '检查中…';
    this.requestJson('GET', '/api/health', '', (s: string, ok: boolean) => {
      this.healthy = ok;
      if (!ok) {
        this.healthText = '✘ sidecar 未连接';
        return;
      }
      try {
        const h = JSON.parse(s) as HealthResp;
        this.modelName = h.model ?? '';
        this.healthText = '✔ 已连接';
      } catch (e) {
        this.healthText = '✘ 响应解析失败';
      }
    });
  }

  startRun(kind: string): void {
    const q = kind === 'diag' ? this.diagQuery : (kind === 'perf' ? this.perfQuery : this.kbQuery);
    if (q.trim() === '') {
      return;
    }
    const bodyObj: Record<string, string> = { 'agent': kind, 'query': q };
    this.requestJson('POST', '/api/run', JSON.stringify(bodyObj), (s: string, ok: boolean) => {
      try {
        const r = JSON.parse(s) as RunResp;
        if (ok && r.id !== undefined && r.id !== '') {
          if (kind === 'diag') {
            this.diagTaskId = r.id;
            this.diagRunning = true;
            this.diagDone = false;
            this.diagStartAt = Date.now();
            this.stepsDiag = [];
            this.thinkDiag = [];
            this.diagResult = '';
          } else if (kind === 'perf') {
            this.perfTaskId = r.id;
            this.perfRunning = true;
            this.perfDone = false;
            this.perfStartAt = Date.now();
            this.stepsPerf = [];
            this.thinkPerf = [];
            this.perfResult = '';
          } else {
            this.kbTaskId = r.id;
            this.kbRunning = true;
            this.kbDone = false;
            this.kbStartAt = Date.now();
            this.stepsKb = [];
            this.thinkKb = [];
            this.kbResult = '';
          }
        }
      } catch (e) {
        // ignore malformed response
      }
    });
  }

  pollKind(kind: string): void {
    const id = kind === 'diag' ? this.diagTaskId : (kind === 'perf' ? this.perfTaskId : this.kbTaskId);
    const running = kind === 'diag' ? this.diagRunning : (kind === 'perf' ? this.perfRunning : this.kbRunning);
    if (id === '' || !running) {
      return;
    }
    this.requestJson('GET', '/api/task/' + id, '', (s: string, ok: boolean) => {
      let t: TaskInfo;
      try {
        t = JSON.parse(s) as TaskInfo;
      } catch (e) {
        return;
      }
      const steps: ToolEvent[] = [];
      const thinks: ToolEvent[] = [];
      t.events.forEach((ev: ToolEvent) => {
        if (ev.type === 'thought') {
          thinks.push(ev);
        } else {
          steps.push(ev);
        }
      });
      if (kind === 'diag') {
        this.stepsDiag = steps;
        this.thinkDiag = thinks;
      } else if (kind === 'perf') {
        this.stepsPerf = steps;
        this.thinkPerf = thinks;
      } else {
        this.stepsKb = steps;
        this.thinkKb = thinks;
      }
      if (t.state === 'done') {
        if (kind === 'diag') {
          this.diagResult = t.result ?? '';
          this.diagRunning = false;
          this.diagDone = true;
        } else if (kind === 'perf') {
          this.perfResult = t.result ?? '';
          this.perfRunning = false;
          this.perfDone = true;
        } else {
          this.kbResult = t.result ?? '';
          this.kbRunning = false;
          this.kbDone = true;
        }
      } else if (t.state === 'error') {
        const errEv: ToolEvent = { type: 'tool_error', detail: '任务失败: ' + (t.error ?? '') };
        steps.push(errEv);
        if (kind === 'diag') {
          this.stepsDiag = steps;
          this.diagRunning = false;
          this.diagDone = true;
        } else if (kind === 'perf') {
          this.stepsPerf = steps;
          this.perfRunning = false;
          this.perfDone = true;
        } else {
          this.stepsKb = steps;
          this.kbRunning = false;
          this.kbDone = true;
        }
      }
    });
  }

  adoptRunning(): void {
    this.requestJson('GET', '/api/health', '', (s: string, ok: boolean) => {
      if (!ok) {
        return;
      }
      let h: HealthResp;
      try {
        h = JSON.parse(s) as HealthResp;
      } catch (e) {
        return;
      }
      const rd = h.recent?.diag ?? '';
      const rp = h.recent?.perf ?? '';
      const rk = h.recent?.kb ?? '';
      if (rd !== '' && this.diagTaskId === '' && !this.diagRunning
          && !this.consumedDiag.includes(rd)) {
        this.diagTaskId = rd;
        this.diagRunning = true;
        this.diagStartAt = Date.now();
        this.consumedDiag.push(rd);
      }
      if (rp !== '' && this.perfTaskId === '' && !this.perfRunning
          && !this.consumedPerf.includes(rp)) {
        this.perfTaskId = rp;
        this.perfRunning = true;
        this.perfStartAt = Date.now();
        this.consumedPerf.push(rp);
      }
      if (rk !== '' && this.kbTaskId === '' && !this.kbRunning
          && !this.consumedKb.includes(rk)) {
        this.kbTaskId = rk;
        this.kbRunning = true;
        this.kbStartAt = Date.now();
        this.consumedKb.push(rk);
      }
    });
  }

  clearTab(kind: string): void {
    if (kind === 'diag') {
      if (this.diagTaskId !== '') {
        this.consumedDiag.push(this.diagTaskId);
      }
      this.diagTaskId = '';
      this.diagRunning = false;
      this.diagDone = false;
      this.stepsDiag = [];
      this.thinkDiag = [];
      this.diagResult = '';
    } else if (kind === 'perf') {
      if (this.perfTaskId !== '') {
        this.consumedPerf.push(this.perfTaskId);
      }
      this.perfTaskId = '';
      this.perfRunning = false;
      this.perfDone = false;
      this.stepsPerf = [];
      this.thinkPerf = [];
      this.perfResult = '';
    } else {
      if (this.kbTaskId !== '') {
        this.consumedKb.push(this.kbTaskId);
      }
      this.kbTaskId = '';
      this.kbRunning = false;
      this.kbDone = false;
      this.stepsKb = [];
      this.thinkKb = [];
      this.kbResult = '';
    }
  }

  build() {
    Column() {
      Row() {
        Column() {
          Text('OHOS 智能体中心')
            .fontSize(20)
            .fontWeight(FontWeight.Bold)
            .fontColor(C_TXT)
          Text('openjiuwen · 设备端诊断 · 体检 · 知识库')
            .fontSize(10)
            .fontColor(C_SUB)
            .margin({ top: 2 })
        }
        .alignItems(HorizontalAlign.Start)
        .layoutWeight(1)
        Blank()
        Column() {
          Circle({ width: 8, height: 8 })
            .fill(this.healthy ? C_OK : C_ERR)
          Text(this.modelName === '' ? 'offline' : this.modelName)
            .fontSize(9)
            .fontColor(C_SUB)
            .margin({ top: 3 })
        }
        .alignItems(HorizontalAlign.Center)
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 12, bottom: 6 })
      Row() {
        TextInput({ text: this.serverUrl })
          .onChange((v: string) => { this.serverUrl = v; })
          .layoutWeight(1)
          .height(34)
          .fontSize(11)
          .backgroundColor(C_CARD)
          .borderRadius(10)
        Button('连接')
          .height(34)
          .fontSize(12)
          .margin({ left: 8 })
          .backgroundColor(C_ACCENT)
          .borderRadius(10)
          .onClick(() => { this.checkHealth(); })
      }
      .width('100%')
      .padding({ left: 16, right: 16, bottom: 4 })
      Tabs({ barPosition: BarPosition.Start }) {
        TabContent() {
          this.agentPane('应用诊断', 'diag')
        }.tabBar('🩺 诊断')
        TabContent() {
          this.agentPane('性能体检', 'perf')
        }.tabBar('⚡ 体检')
        TabContent() {
          this.agentPane('知识库', 'kb')
        }.tabBar('📚 知识库')
      }
      .layoutWeight(1)
      .width('100%')
      .barHeight(40)
    }
    .height('100%')
    .width('100%')
    .backgroundColor(C_BG)
  }

  @State showThinkDiag: boolean = true;
  @State showThinkPerf: boolean = true;
  @State showThinkKb: boolean = true;

  stepsOf(tag: string): ToolEvent[] {
    return tag === 'diag' ? this.stepsDiag : (tag === 'perf' ? this.stepsPerf : this.stepsKb);
  }

  thinkOf(tag: string): ToolEvent[] {
    return tag === 'diag' ? this.thinkDiag : (tag === 'perf' ? this.thinkPerf : this.thinkKb);
  }

  resultOf(tag: string): string {
    return tag === 'diag' ? this.diagResult : (tag === 'perf' ? this.perfResult : this.kbResult);
  }

  isRunning(tag: string): boolean {
    return tag === 'diag' ? this.diagRunning : (tag === 'perf' ? this.perfRunning : this.kbRunning);
  }

  isDone(tag: string): boolean {
    return tag === 'diag' ? this.diagDone : (tag === 'perf' ? this.perfDone : this.kbDone);
  }

  startAtOf(tag: string): number {
    return tag === 'diag' ? this.diagStartAt : (tag === 'perf' ? this.perfStartAt : this.kbStartAt);
  }

  showThinkOf(tag: string): boolean {
    return tag === 'diag' ? this.showThinkDiag : (tag === 'perf' ? this.showThinkPerf : this.showThinkKb);
  }

  toggleThink(tag: string): void {
    if (tag === 'diag') {
      this.showThinkDiag = !this.showThinkDiag;
    } else if (tag === 'perf') {
      this.showThinkPerf = !this.showThinkPerf;
    } else {
      this.showThinkKb = !this.showThinkKb;
    }
  }

  elapsedText(tag: string): string {
    const sec = Math.floor((Date.now() - this.startAtOf(tag)) / 1000);
    return '⏱ 已运行 ' + sec + 's';
  }

  stateText(tag: string): string {
    if (this.isRunning(tag)) {
      return '运行中';
    }
    if (this.isDone(tag)) {
      return '已完成';
    }
    return '空闲';
  }

  stateColor(tag: string): string {
    if (this.isRunning(tag)) {
      return C_WARN;
    }
    if (this.isDone(tag)) {
      return C_OK;
    }
    return C_SUB;
  }

  @Builder
  eventRow(e: ToolEvent) {
    Column() {
      if (e.type === 'cmd') {
        Row() {
          Text('❯').fontSize(11).fontColor(C_ACCENT).fontWeight(FontWeight.Bold)
          Text(e.cmd ?? '').fontSize(10).fontColor('#c4cede')
            .margin({ left: 6 }).layoutWeight(1).maxLines(2)
          Text((e.ms ?? 0) + 'ms').fontSize(9).fontColor(C_SUB).margin({ left: 6 })
        }.width('100%')
      } else if (e.type === 'tool_start') {
        Row() {
          Text('▶').fontSize(10).fontColor(C_ACCENT)
          Text('调用 ' + (e.tool ?? '') + (e.args ?? '')).fontSize(11).fontColor(C_TXT)
            .margin({ left: 6 }).layoutWeight(1).maxLines(1)
        }.width('100%')
      } else if (e.type === 'tool_done') {
        Row() {
          Text('✔').fontSize(10).fontColor(C_OK)
          Text((e.tool ?? '') + ' 完成').fontSize(11).fontColor(C_OK)
            .margin({ left: 6 }).layoutWeight(1).maxLines(1)
          Text((e.ms ?? 0) + 'ms').fontSize(9).fontColor(C_OK)
        }.width('100%')
        if ((e.preview ?? '') !== '') {
          Text('  └ ' + (e.preview ?? '').split('\n')[0]).fontSize(10).fontColor(C_SUB)
            .width('100%').maxLines(1).margin({ top: 2 })
        }
      } else if (e.type === 'tool_error') {
        Row() {
          Text('✘').fontSize(10).fontColor(C_ERR)
          Text((e.detail ?? '') + (e.tool ?? '')).fontSize(11).fontColor(C_ERR)
            .margin({ left: 6 }).layoutWeight(1).maxLines(2)
        }.width('100%')
      }
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .padding({ top: 3, bottom: 3 })
  }

  @Builder
  thinkRow(t: ToolEvent, idx: number) {
    Column() {
      Row() {
        Text('💡 第' + (t.iter ?? (idx + 1)) + '步 · 思考')
          .fontSize(10).fontColor(C_THINK).fontWeight(FontWeight.Bold)
        if ((t.plan ?? '') !== '') {
          Text('→ 计划调用: ' + (t.plan ?? '')).fontSize(9).fontColor(C_ACCENT)
            .margin({ left: 8 }).layoutWeight(1).maxLines(1)
        }
      }
      .width('100%')
      if ((t.text ?? '') !== '') {
        Text(t.text ?? '').fontSize(11).fontColor('#cdd5e0')
          .width('100%').margin({ top: 3 })
      }
      Divider().color('#232a35').margin({ top: 6 })
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .padding({ top: 5 })
  }

  @Builder
  agentPane(title: string, tag: string) {
    Column() {
      TextArea({ text: tag === 'diag' ? this.diagQuery : (tag === 'perf' ? this.perfQuery : this.kbQuery),
        placeholder: '输入诊断、体检或知识库任务' })
        .height(64)
        .fontSize(11)
        .fontColor(C_TXT)
        .backgroundColor(C_CARD2)
        .borderRadius(12)
        .onChange((v: string) => {
          if (tag === 'diag') {
            this.diagQuery = v;
          } else if (tag === 'perf') {
            this.perfQuery = v;
          } else {
            this.kbQuery = v;
          }
        })
      Row() {
        Button(this.isRunning(tag) ? '运行中…' : '开始' + title)
          .enabled(!this.isRunning(tag))
          .height(36)
          .fontSize(13)
          .fontWeight(FontWeight.Medium)
          .layoutWeight(1)
          .backgroundColor(this.isRunning(tag) ? '#3a4250' : C_ACCENT)
          .borderRadius(12)
          .onClick(() => { this.startRun(tag); })
        Button('清空')
          .height(36)
          .fontSize(12)
          .margin({ left: 8 })
          .backgroundColor(C_CARD)
          .fontColor(C_SUB)
          .borderRadius(12)
          .onClick(() => { this.clearTab(tag); })
      }
      .width('100%')
      .margin({ top: 8 })
      Row() {
        Circle({ width: 7, height: 7 }).fill(this.stateColor(tag))
        Text(this.stateText(tag)).fontSize(11).fontColor(this.stateColor(tag)).margin({ left: 6 })
        if (this.isRunning(tag)) {
          Text(this.elapsedText(tag)).fontSize(10).fontColor(C_WARN).margin({ left: 10 })
        }
        Blank()
        Text('tick' + this.tick.toFixed(0)).fontSize(1).fontColor(C_BG)
      }
      .width('100%')
      .margin({ top: 8 })
      Column() {
        Row() {
          Text('💡 思考过程').fontSize(12).fontWeight(FontWeight.Bold).fontColor(C_TXT)
          Blank()
          Text((this.thinkOf(tag).length > 0 ? this.thinkOf(tag).length + ' 步 · ' : '')
            + (this.showThinkOf(tag) ? '收起 ▴' : '展开 ▾'))
            .fontSize(10).fontColor(C_SUB)
        }
        .width('100%')
        .onClick(() => { this.toggleThink(tag); })
        if (this.showThinkOf(tag)) {
          Scroll() {
            Column() {
              ForEach(this.thinkOf(tag), (t: ToolEvent, idx: number) => {
                this.thinkRow(t, idx)
              }, (t: ToolEvent, idx: number) => idx.toString() + 't')
              if (this.thinkOf(tag).length === 0) {
                Text(this.isRunning(tag) ? '等待 LLM 推理…' : '(暂无)')
                  .fontSize(10).fontColor(C_SUB).width('100%')
              }
            }
            .width('100%')
          }
          .height(118)
          .width('100%')
          .margin({ top: 6 })
        }
      }
      .backgroundColor(C_CARD)
      .borderRadius(14)
      .padding(10)
      .width('100%')
      .margin({ top: 8 })
      Column() {
        Text('📋 执行步骤 (' + this.stepsOf(tag).length + ')')
          .fontSize(12).fontWeight(FontWeight.Bold).fontColor(C_TXT).width('100%')
        Scroll() {
          Column() {
            ForEach(this.stepsOf(tag), (e: ToolEvent, idx: number) => {
              this.eventRow(e)
            }, (e: ToolEvent, idx: number) => idx.toString() + 's')
            if (this.stepsOf(tag).length === 0) {
              Text(this.isRunning(tag) ? '等待执行…' : '(暂无)')
                .fontSize(10).fontColor(C_SUB).width('100%')
            }
          }
          .width('100%')
        }
        .height(96)
        .width('100%')
        .margin({ top: 6 })
      }
      .backgroundColor(C_CARD)
      .borderRadius(14)
      .padding(10)
      .width('100%')
      .margin({ top: 8 })
      Column() {
        Text('📄 ' + title + '报告').fontSize(12).fontWeight(FontWeight.Bold)
          .fontColor(C_TXT).width('100%')
        Scroll() {
          Column() {
            Text(this.resultText(tag)).fontSize(12).fontColor(C_TXT).width('100%')
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
          .padding({ bottom: 10 })
        }
        .layoutWeight(1)
        .width('100%')
        .margin({ top: 6 })
      }
      .backgroundColor(C_CARD)
      .borderRadius(14)
      .padding(10)
      .width('100%')
      .layoutWeight(1)
      .margin({ top: 8, bottom: 10 })
    }
    .width('100%')
    .height('100%')
    .padding({ left: 12, right: 12, top: 4 })
  }

  resultText(tag: string): string {
    const raw = this.resultOf(tag);
    if (raw !== '') {
      return raw;
    }
    if (this.isRunning(tag)) {
      return '智能体正在执行，可在上方查看实时思考与执行步骤…';
    }
    return '暂无报告。点击「开始任务」或等待接管运行中的任务。';
  }
}
