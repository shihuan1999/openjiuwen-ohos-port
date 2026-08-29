// openJiuwen 知识库 — K3 pico (OHOS 6.1) 1920x1080 横屏
// 设计规范: openjiuwen-ohos-port/kbapp-design/DESIGN_SPEC.md
// 后端: sidecar v3 http://127.0.0.1:8765 (docs/search/notes/store/sync/run)
//       memory_server http://127.0.0.1:8000 (长期记忆)
import http from '@ohos.net.http';
import promptAction from '@ohos.promptAction';

const BASE = 'http://127.0.0.1:8765';
const MEM_BASE = 'http://127.0.0.1:8000';

// ---- 色彩令牌 (DESIGN_SPEC §2, 亮色) ----
class C {
  static readonly primary: string = '#0A59F7';
  static readonly primaryContainer: string = '#EBF1FF';
  static readonly accent: string = '#E84026';
  static readonly success: string = '#00B42A';
  static readonly successContainer: string = '#E8FFEA';
  static readonly warning: string = '#FF7D00';
  static readonly warningContainer: string = '#FFF3E8';
  static readonly error: string = '#F53F3F';
  static readonly errorContainer: string = '#FFECE8';
  static readonly localOnly: string = '#86909C';
  static readonly page: string = '#F1F3F5';
  static readonly surface: string = '#FFFFFF';
  static readonly surface2: string = '#F7F8FA';
  static readonly surface3: string = '#EFF1F4';
  static readonly stroke1: string = '#E5E8EC';
  static readonly stroke2: string = '#D4D9E0';
  static readonly textP: string = '#182433';
  static readonly textS: string = '#4E5969';
  static readonly textT: string = '#86909C';
  static readonly inverse: string = '#FFFFFF';
  static readonly codeBg: string = '#F2F3F7';
}

// ---- 数据模型 ----
interface KbDoc {
  id: string; title: string; category: string; tags: string[];
  content?: string; summary: string; source: string; origin: string;
  favorite: number; rev: number; created_at: number; updated_at: number;
  notes?: KbNote[]; viewed_at?: number;
}
interface CatInfo { category: string; count: number }
interface SearchHit {
  doc_id: string; title: string; head: string; category: string;
  snippet: string; score: number;
}
interface StoreItem {
  id: string; name: string; repo: string; category: string;
  status: string; desc: string; deps: string; capabilities: string[];
  local_module?: string; runnable: boolean; enabled: boolean;
  run_count: number; last_run_at: number;
}
interface StoreStats { installed: number; cloud: number; na: number; total: number }
interface SyncLog { at: number; line: string }
interface SyncInfo {
  ok: boolean | null; at: number; detail: string; pushed: number; pulled: number;
  conflicts: number; cloud_url: string; pending: number; last_sync_at: number | null;
  device_id: string; logs: SyncLog[];
}
interface KbStats { docs: number; favorites: number; notes: number; chunks: number; categories: number }
interface TaskEv { type: string; node?: string; head?: string; tool?: string; ms?: number }
interface TaskInfo {
  id: string; agent: string; state: string; query: string;
  events: TaskEv[]; result: string; error: string; elapsed: number;
}
interface MemItem { content?: string; type?: string; score?: number }
interface KbNote {
  id: string; doc_id: string; quote: string; note: string;
  tags: string[]; created_at: number; updated_at: number;
  doc_title?: string;
}

// ---- 请求体 (arkts-no-untyped-obj-literals) ----
interface RunBody { agent: string; query: string }
interface DocBody { title: string; content: string; category: string; tags: string[] }
interface NoteBody { doc_id: string; quote: string; note: string; tags: string[] }
interface ToggleBody { id: string; enabled: boolean }
interface SyncBody { direction: string }
interface UrlBody { url: string }
interface MemBody { query: string; user_id: string; scope_id: string; num: number; threshold: number }

class MdBlock {
  type: string = 'p';
  text: string = '';
}

function mdParse(src: string): MdBlock[] {
  const out: MdBlock[] = [];
  const lines: string[] = (src || '').split('\n');
  let inCode: boolean = false;
  let codeBuf: string[] = [];
  for (let li = 0; li < lines.length; li++) {
    const line: string = lines[li];
    if (line.trimStart().startsWith('```')) {
      if (inCode) {
        const b = new MdBlock();
        b.type = 'code';
        b.text = codeBuf.join('\n');
        out.push(b);
        codeBuf = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }
    const t: string = line.trim();
    if (t.startsWith('# ')) {
      const b = new MdBlock(); b.type = 'h1'; b.text = t.slice(2); out.push(b);
    } else if (t.startsWith('## ')) {
      const b = new MdBlock(); b.type = 'h2'; b.text = t.slice(3); out.push(b);
    } else if (t.startsWith('- ') || t.startsWith('* ')) {
      const b = new MdBlock(); b.type = 'li'; b.text = t.slice(2); out.push(b);
    } else if (t.length > 0) {
      const b = new MdBlock(); b.type = 'p'; b.text = t; out.push(b);
    }
  }
  if (inCode && codeBuf.length > 0) {
    const b = new MdBlock();
    b.type = 'code';
    b.text = codeBuf.join('\n');
    out.push(b);
  }
  return out;
}

function fmtTime(ts: number | null | undefined): string {
  if (!ts) { return ''; }
  const d = new Date(ts * 1000);
  const p = (n: number): string => n < 10 ? '0' + n : '' + n;
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ---- REST ----
function req(method: http.RequestMethod, url: string, bodyJson: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const h = http.createHttp();
    const header: Record<string, string> = { 'Content-Type': 'application/json' };
    const opts: http.HttpRequestOptions = {
      method: method,
      connectTimeout: 8000,
      readTimeout: 30000,
      header: header,
      expectDataType: http.HttpDataType.STRING,
    };
    if (bodyJson.length > 0) {
      opts.extraData = bodyJson;
    }
    h.request(url, opts).then((r: http.HttpResponse) => {
      h.destroy();
      const code: number = r.responseCode;
      const res: string = (r.result === undefined || r.result === null) ? '' : `${r.result}`;
      if (code >= 200 && code < 300) {
        resolve(res);
      } else {
        reject(`HTTP ${code} ${res.slice(0, 120)}`);
      }
    }).catch((e: Error) => {
      h.destroy();
      reject(`net:${e.message}`);
    });
  });
}

function parseObj(s: string): object | null {
  try {
    return JSON.parse(s) as object;
  } catch (e) {
    return null;
  }
}

function apiGet(path: string): Promise<object | null> {
  return req(http.RequestMethod.GET, BASE + path, '').then((s: string): object | null => parseObj(s))
    .catch((e: string): object | null => null);
}

function apiSend(method: http.RequestMethod, path: string, bodyJson: string): Promise<object | null> {
  return req(method, BASE + path, bodyJson).then((s: string): object | null => parseObj(s))
    .catch((e: string): object | null => null);
}

function toast(msg: string): void {
  promptAction.showToast({ message: msg, duration: 2500, bottom: '96vp' });
}

// sidecar 事件 -> 用户可读步骤名 (视觉评审 P0: 杜绝 step/done 裸文案)
function evLabel(e: TaskEv): string {
  if (e.type === 'done') { return '研究完成'; }
  if (e.type === 'cmd') { return '任务启动'; }
  if (e.type === 'tool_start') { return `调用工具 ${e.tool !== undefined ? e.tool : ''}`; }
  if (e.type === 'tool_done') { return `工具返回 ${e.tool !== undefined ? e.tool : ''}`; }
  if (e.type === 'tool_error') { return `工具出错 ${e.tool !== undefined ? e.tool : ''}`; }
  if (e.type === 'node') {
    if (e.node === 'step') { return '研究步骤'; }
    if (e.head !== undefined && e.head.length > 0) { return '研究进展'; }
    return '工作流节点';
  }
  return e.type;
}

@Entry
@Component
struct Index {
  @State tabIdx: number = 0;
  // Tab1 知识库
  @State docs: KbDoc[] = [];
  @State cats: CatInfo[] = [];
  @State tagsAll: string[] = [];
  @State historyDocs: KbDoc[] = [];
  @State curCat: string = '全部';
  @State searchQ: string = '';
  @State searchMode: string = 'keyword';
  @State searching: boolean = false;
  @State searchHits: SearchHit[] = [];
  @State loadingDocs: boolean = false;
  @State curDoc: KbDoc | null = null;
  @State showNewDoc: boolean = false;
  @State ndTitle: string = '';
  @State ndCat: string = '';
  @State ndTags: string = '';
  @State ndContent: string = '';
  @State noteEdit: string = '';
  // Tab2 AI 深研
  @State agentKind: string = 'research';
  @State rQuery: string = '';
  @State rTaskId: string = '';
  @State rState: string = 'idle';
  @State rEvents: TaskEv[] = [];
  @State rResult: string = '';
  @State rError: string = '';
  @State rElapsed: number = 0;
  @State citations: SearchHit[] = [];
  @State agentOptions: string[] = ['research', 'kb', 'career', 'diag', 'perf'];
  // Tab3 笔记
  @State notes: KbNote[] = [];
  @State curNote: KbNote | null = null;
  @State noteQ: string = '';
  @State curTag: string = '';
  // Tab4 商店
  @State storeItems: StoreItem[] = [];
  @State storeStats: StoreStats | null = null;
  @State storeFilter: string = '全部';
  @State storeDetail: StoreItem | null = null;
  // Tab5 我的
  @State sync: SyncInfo | null = null;
  @State stats: KbStats | null = null;
  @State mems: MemItem[] = [];
  @State memQ: string = '';
  @State cloudUrlEdit: string = '';
  @State syncing: boolean = false;
  private pollTimer: number = -1;

  aboutToAppear(): void {
    this.reloadAll();
  }

  aboutToDisappear(): void {
    if (this.pollTimer >= 0) {
      clearInterval(this.pollTimer);
      this.pollTimer = -1;
    }
  }

  reloadAll(): void {
    this.loadDocs();
    this.loadMeta();
    this.loadStore();
    this.loadSync();
    this.loadNotes();
  }

  // ---------- 数据加载 ----------
  loadDocs(): void {
    this.loadingDocs = true;
    const q: string = this.searchQ.trim();
    let path: string = '/api/kb/docs?limit=200';
    if (this.curCat !== '全部') {
      path += `&category=${encodeURIComponent(this.curCat)}`;
    }
    if (q) {
      path += `&q=${encodeURIComponent(q)}`;
    }
    apiGet(path).then((r: object | null) => {
      this.loadingDocs = false;
      if (r !== null) {
        const o = r as Record<string, Object>;
        const d = o['docs'] as KbDoc[];
        this.docs = d === undefined ? [] : d;
      }
    });
  }

  loadMeta(): void {
    apiGet('/api/kb/meta').then((r: object | null) => {
      if (r === null) { return; }
      const o = r as Record<string, Object>;
      const c = o['categories'] as CatInfo[];
      this.cats = c === undefined ? [] : c;
      const h = o['history'] as KbDoc[];
      this.historyDocs = h === undefined ? [] : h;
      const tg = o['tags'] as string[][];
      const raw: string[][] = tg === undefined ? [] : tg;
      this.tagsAll = raw.map((p: string[]): string => p[0]).slice(0, 10);
      this.stats = o['stats'] as KbStats;
    });
  }

  openDoc(id: string): void {
    apiGet('/api/kb/doc/' + id).then((r: object | null) => {
      if (r === null) { toast('文档加载失败'); return; }
      const o = r as Record<string, Object>;
      this.curDoc = o['doc'] as KbDoc;
      this.loadMeta();
    });
  }

  doSearch(): void {
    const q: string = this.searchQ.trim();
    if (!q) {
      this.searchHits = [];
      this.loadDocs();
      return;
    }
    this.searching = true;
    const mode: string = this.searchMode === 'semantic' ? 'semantic' : 'keyword';
    apiGet(`/api/kb/search?q=${encodeURIComponent(q)}&mode=${mode}&top_k=8`).then((r: object | null) => {
      this.searching = false;
      if (r !== null) {
        const o = r as Record<string, Object>;
        const hits = o['hits'] as SearchHit[];
        this.searchHits = hits === undefined ? [] : hits;
      }
    });
  }

  createDoc(): void {
    if (!this.ndTitle.trim()) { toast('请输入标题'); return; }
    const body: DocBody = {
      title: this.ndTitle,
      content: this.ndContent,
      category: this.ndCat || '未分类',
      tags: this.ndTags.split(/[,，\s]+/).filter((t: string): boolean => t.length > 0),
    };
    apiSend(http.RequestMethod.POST, '/api/kb/docs', JSON.stringify(body))
      .then((r: object | null) => {
        if (r !== null) {
          toast('已创建');
          this.showNewDoc = false;
          this.ndTitle = ''; this.ndCat = ''; this.ndTags = ''; this.ndContent = '';
          this.loadDocs();
          this.loadMeta();
        } else {
          toast('创建失败');
        }
      });
  }

  toggleFav(id: string): void {
    apiSend(http.RequestMethod.POST, `/api/kb/doc/${id}/favorite`, '{}')
      .then((r: object | null) => {
        if (this.curDoc !== null && this.curDoc.id === id) {
          const nd: KbDoc = JSON.parse(JSON.stringify(this.curDoc)) as KbDoc;
          nd.favorite = nd.favorite === 1 ? 0 : 1;
          this.curDoc = nd;
        }
        this.loadDocs();
      });
  }

  addNoteFor(docId: string): void {
    if (!this.noteEdit.trim()) { toast('请输入批注内容'); return; }
    const quote: string = this.curDoc !== null ? (this.curDoc.summary || '').slice(0, 120) : '';
    const body: NoteBody = { doc_id: docId, quote: quote, note: this.noteEdit, tags: [] };
    apiSend(http.RequestMethod.POST, '/api/kb/notes', JSON.stringify(body))
      .then((r: object | null) => {
        if (r !== null) {
          toast('笔记已添加');
          this.noteEdit = '';
          this.openDoc(docId);
          this.loadNotes();
        } else {
          toast('笔记失败');
        }
      });
  }

  loadNotes(): void {
    let path: string = '/api/kb/notes?limit=200';
    if (this.noteQ.trim()) {
      path += `&q=${encodeURIComponent(this.noteQ)}`;
    }
    apiGet(path).then((r: object | null) => {
      if (r !== null) {
        const o = r as Record<string, Object>;
        const ns0 = o['notes'] as KbNote[];
        let ns: KbNote[] = ns0 === undefined ? [] : ns0;
        if (this.curTag) {
          const t: string = this.curTag;
          ns = ns.filter((n: KbNote): boolean =>
            n.tags.indexOf(t) >= 0 || (n.note + n.quote).indexOf(t) >= 0);
        }
        this.notes = ns;
      }
    });
  }

  delNote(id: string): void {
    req(http.RequestMethod.DELETE, BASE + '/api/kb/note/' + id, '')
      .then(() => {
        this.curNote = null;
        this.loadNotes();
        toast('已删除');
      })
      .catch(() => toast('删除失败'));
  }

  // ---------- Agent 运行 ----------
  startResearch(): void {
    const q: string = this.rQuery.trim();
    if (!q) { toast('请输入研究问题'); return; }
    const body: RunBody = { agent: this.agentKind, query: q };
    apiSend(http.RequestMethod.POST, '/api/run', JSON.stringify(body))
      .then((r: object | null) => {
        if (r === null) { toast('启动失败（检查 sidecar）'); return; }
        const o = r as Record<string, Object>;
        const id = o['id'] as string;
        const err = o['error'] as string;
        if (id === undefined) {
          toast(err !== undefined ? err : '启动失败');
          return;
        }
        this.rTaskId = id;
        this.rState = 'running';
        this.rEvents = [];
        this.rResult = '';
        this.rError = '';
        apiGet(`/api/kb/search?q=${encodeURIComponent(q)}&mode=semantic&top_k=5`)
          .then((rr: object | null) => {
            if (rr !== null) {
              const oo = rr as Record<string, Object>;
              const hits = oo['hits'] as SearchHit[];
              this.citations = hits === undefined ? [] : hits;
            }
          });
        if (this.pollTimer >= 0) { clearInterval(this.pollTimer); }
        this.pollTimer = setInterval(() => this.pollTask(), 2000);
      });
  }

  pollTask(): void {
    if (!this.rTaskId || this.rState !== 'running') { return; }
    apiGet('/api/task/' + this.rTaskId).then((r: object | null) => {
      if (r === null) { return; }
      const t = r as TaskInfo;
      this.rEvents = t.events === undefined ? [] : t.events;
      this.rElapsed = t.elapsed === undefined ? 0 : t.elapsed;
      if (t.state !== 'running') {
        this.rState = t.state;
        this.rResult = t.result === undefined ? '' : t.result;
        this.rError = t.error === undefined ? '' : t.error;
        if (this.pollTimer >= 0) {
          clearInterval(this.pollTimer);
          this.pollTimer = -1;
        }
        this.loadStore();
        toast(t.state === 'done' ? `运行完成 · ${Math.round(t.elapsed)}s` : '运行出错');
      }
    });
  }

  saveReportAsDoc(): void {
    if (!this.rResult) { return; }
    const body: DocBody = {
      title: `[${this.agentKind}] ${this.rQuery.slice(0, 40)}`,
      content: this.rResult,
      category: '报告',
      tags: ['深研', this.agentKind],
    };
    apiSend(http.RequestMethod.POST, '/api/kb/docs', JSON.stringify(body))
      .then((r: object | null) => {
        if (r !== null) {
          toast('已保存到知识库 · 报告');
          this.loadDocs();
          this.loadMeta();
        } else {
          toast('保存失败');
        }
      });
  }

  // ---------- 商店 ----------
  loadStore(): void {
    apiGet('/api/store').then((r: object | null) => {
      if (r === null) { return; }
      const o = r as Record<string, Object>;
      const items = o['items'] as StoreItem[];
      this.storeItems = items === undefined ? [] : items;
      this.storeStats = o['stats'] as StoreStats;
    });
  }

  toggleAgent(it: StoreItem): void {
    const body: ToggleBody = { id: it.id, enabled: !it.enabled };
    apiSend(http.RequestMethod.POST, '/api/store/toggle', JSON.stringify(body))
      .then((r: object | null) => {
        if (r !== null) {
          toast(it.enabled ? `已禁用 ${it.name}` : `已启用 ${it.name}`);
          this.loadStore();
        } else {
          toast('操作失败');
        }
      });
  }

  runAgent(it: StoreItem): void {
    const kindMap: Record<string, string> = {
      'research': 'research', 'kb': 'kb', 'career': 'career',
      'diag': 'diag', 'perf': 'perf',
    };
    const kind: string = kindMap[it.id] !== undefined ? kindMap[it.id] : '';
    if (!kind) { toast('该组件不可直接运行'); return; }
    this.agentKind = kind;
    this.rQuery = '';
    this.tabIdx = 1;
    toast(`已切换到 AI 深研 · ${it.name}`);
  }

  // ---------- 同步 ----------
  loadSync(): void {
    apiGet('/api/sync/status').then((r: object | null) => {
      if (r !== null) {
        this.sync = r as SyncInfo;
        this.cloudUrlEdit = this.sync.cloud_url || '';
      }
    });
  }

  doSync(): void {
    this.syncing = true;
    const body: SyncBody = { direction: 'both' };
    apiSend(http.RequestMethod.POST, '/api/sync/now', JSON.stringify(body))
      .then((r: object | null) => {
        this.syncing = false;
        if (r !== null) {
          const o = r as Record<string, Object>;
          if (o['ok'] === true) {
            const pushed = o['pushed'] as number;
            const pulled = o['pulled'] as number;
            const conflicts = o['conflicts'] as number;
            toast(`同步完成 ↑${pushed} ↓${pulled} 冲突${conflicts}`);
          } else {
            const det = o['detail'] as string;
            toast(`同步失败: ${det !== undefined ? det.slice(0, 60) : ''}`);
          }
        } else {
          toast('同步失败（云端不可达）');
        }
        this.loadSync();
        this.loadDocs();
        this.loadMeta();
      });
  }

  saveCloudUrl(): void {
    const body: UrlBody = { url: this.cloudUrlEdit };
    apiSend(http.RequestMethod.POST, '/api/sync/cloud-url', JSON.stringify(body))
      .then((r: object | null) => {
        toast(r !== null ? '云端地址已保存' : '地址无效');
        this.loadSync();
      });
  }

  loadMems(): void {
    const body: MemBody = {
      query: this.memQ || 'K3',
      user_id: 'kb-agent',
      scope_id: 'default',
      num: 20,
      threshold: 0.05,
    };
    req(http.RequestMethod.POST, MEM_BASE + '/search_memory/', JSON.stringify(body))
      .then((s: string) => {
        const o = parseObj(s);
        if (o !== null) {
          const oo = o as Record<string, Object>;
          const res = oo['results'] as MemItem[];
          this.mems = res === undefined ? [] : res;
        } else {
          this.mems = [];
        }
      })
      .catch(() => {
        this.mems = [];
      });
  }

  // =========================================================
  build() {
    Row() {
      this.Rail()
      Column() {
        if (this.tabIdx === 0) {
          this.PageLibrary()
        } else if (this.tabIdx === 1) {
          this.PageResearch()
        } else if (this.tabIdx === 2) {
          this.PageNotes()
        } else if (this.tabIdx === 3) {
          this.PageStore()
        } else {
          this.PageMe()
        }
      }
      .layoutWeight(1)
      .height('100%')
      .backgroundColor(C.page)
    }
    .width('100%').height('100%')
  }

  syncPending(): number {
    return this.sync !== null ? (this.sync.pending || 0) : 0;
  }

  docTags(d: KbDoc): string[] {
    if (d.tags === undefined || d.tags === null) {
      return [];
    }
    return d.tags.slice(0, 3);
  }

  // ---------- 左侧 Navigation Rail (72vp, DESIGN_SPEC §1.2) ----------
  @Builder
  Rail() {
    Column() {
      Column() {
        Text('J')
          .fontSize(24).fontColor(C.inverse).fontWeight(700)
      }
      .width(40).height(40).borderRadius(12)
      .backgroundColor(C.accent).margin({ top: 16, bottom: 24 })
      .justifyContent(FlexAlign.Center)

      this.RailItem(0, '📚', '知识库')
      this.RailItem(1, '🔬', 'AI 深研')
      this.RailItem(2, '✏️', '笔记')
      this.RailItem(3, '🧩', '商店')
      this.RailItem(4, '👤', '我的')

      Blank()

      Column() {
        Circle()
          .width(10).height(10)
          .fill(this.syncing ? C.primary :
            (this.syncPending() > 0 ? C.warning :
              (this.sync !== null && this.sync.ok === false ? C.error :
                (this.sync !== null && this.sync.last_sync_at !== null ? C.success : C.localOnly))))
        if (this.syncPending() > 0) {
          Text(`${this.syncPending()}`)
            .fontSize(10).fontColor(C.warning).margin({ top: 2 })
        } else {
          Text('同步').fontSize(9).fontColor(C.textT).margin({ top: 2 })
        }
      }
      .margin({ bottom: 16 })
      .onClick(() => {
        this.tabIdx = 4;
      })
    }
    .width(72).height('100%').backgroundColor(C.surface)
    .alignItems(HorizontalAlign.Center)
  }

  @Builder
  RailItem(idx: number, icon: string, label: string) {
    Column() {
      Text(icon).fontSize(22)
      Text(label)
        .fontSize(11)
        .fontColor(this.tabIdx === idx ? C.primary : C.textS)
        .margin({ top: 2 })
    }
    .width(72).height(56)
    .backgroundColor(this.tabIdx === idx ? C.primaryContainer : Color.Transparent)
    .borderRadius({ topLeft: 8, topRight: 8, bottomLeft: 8, bottomRight: 8 })
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .onClick(() => {
      this.tabIdx = idx;
    })
    .margin({ bottom: 4 })
  }

  // =========================================================
  // Tab1 知识库 (§6.1 双栏)
  // =========================================================
  @Builder
  PageLibrary() {
    Column() {
      Row() {
        Row() {
          Text('🔍').fontSize(18).margin({ right: 8 })
          TextInput({
            placeholder: this.searchMode === 'semantic' ?
              '用自然语言描述问题，如：K3 pico 如何编译 HAP' : '搜索标题、内容、标签…'
          })
            .onChange((v: string) => {
              this.searchQ = v;
            })
            .onSubmit(() => this.doSearch())
            .layoutWeight(1).height(44).backgroundColor(Color.Transparent)
            .placeholderColor(C.textT).fontSize(16)
          if (this.searchQ.length > 0) {
            Text('×')
              .fontSize(18).fontColor(C.textT).margin({ right: 8 })
              .onClick(() => {
                this.searchQ = '';
                this.searchHits = [];
                this.loadDocs();
              })
          }
          Divider().vertical(true).height(20).color(C.stroke1).margin({ right: 8 })
          Text(this.searchMode === 'semantic' ? '⚡ 语义' : '🔤 关键词')
            .fontSize(13)
            .fontColor(this.searchMode === 'semantic' ? C.primary : C.textS)
            .padding({ left: 8, right: 8 }).height(32)
            .borderRadius(16).backgroundColor(C.surface3)
            .onClick(() => {
              this.searchMode = this.searchMode === 'semantic' ? 'keyword' : 'semantic';
              if (this.searchQ) {
                this.doSearch();
              }
            })
        }
        .width(640).height(48).padding({ left: 16, right: 8 })
        .borderRadius(24).backgroundColor(C.surface2)
        .alignItems(VerticalAlign.Center)

        Blank()

        Button('＋ 新建文档')
          .fontSize(15).fontColor(C.inverse).height(40).padding({ left: 20, right: 20 })
          .backgroundColor(C.primary).borderRadius(20)
          .onClick(() => {
            this.showNewDoc = true;
          })
      }
      .width('100%').height(64).padding({ left: 32, right: 32 })

      if (this.historyDocs.length > 0) {
        Row() {
          Text('最近').fontSize(12).fontColor(C.textT).margin({ right: 8 })
          Scroll() {
            Row() {
              ForEach(this.historyDocs.slice(0, 6), (d: KbDoc) => {
                this.Chip(d.title.slice(0, 12), false, (): void => this.openDoc(d.id))
              }, (d: KbDoc) => 'h' + d.id)
            }
          }
          .scrollable(ScrollDirection.Horizontal).layoutWeight(1).height(32)
        }
        .width('100%').height(48).padding({ left: 32, right: 32 })
      }

      Row() {
        Scroll() {
          Row() {
            this.Chip('全部', this.curCat === '全部', (): void => {
              this.curCat = '全部';
              this.loadDocs();
            })
            ForEach(this.cats, (c: CatInfo) => {
              this.Chip(`${c.category} ${c.count}`, this.curCat === c.category, (): void => {
                this.curCat = c.category;
                this.loadDocs();
              })
            }, (c: CatInfo) => 'c' + c.category)
          }
        }
        .scrollable(ScrollDirection.Horizontal).layoutWeight(1).height(32)
      }
      .width('100%').height(44).padding({ left: 32, right: 32 })

      Row() {
        Column() {
          if (this.searching) {
            this.SkeletonGrid()
          } else if (this.searchQ && this.searchHits.length === 0) {
            this.EmptyState('没有找到相关内容', '换个关键词，或切换到语义检索', '试试语义搜索', (): void => {
              this.searchMode = 'semantic';
              this.doSearch();
            })
          } else if (this.searchQ && this.searchHits.length > 0) {
            Text(`约 ${this.searchHits.length} 条结果 · ${this.searchMode === 'semantic' ? '语义' : '关键词'}模式`)
              .fontSize(12).fontColor(C.textT).margin({ bottom: 8 })
            Scroll() {
              Column() {
                ForEach(this.searchHits, (h: SearchHit) => {
                  this.HitCard(h)
                }, (h: SearchHit) => 's' + h.doc_id + h.score)
              }.width('100%')
            }
            .layoutWeight(1).scrollBar(BarState.Auto)
          } else if (this.loadingDocs) {
            this.SkeletonGrid()
          } else if (this.docs.length === 0) {
            this.EmptyState('知识库还是空的', '把第一份文档放进来吧', '新建文档', (): void => {
              this.showNewDoc = true;
            })
          } else {
            Scroll() {
              Grid() {
                ForEach(this.docs, (d: KbDoc) => {
                  GridItem() {
                    this.DocCard(d)
                  }
                }, (d: KbDoc) => 'doc-' + d.id)
              }
              .columnsTemplate('1fr 1fr')
              .columnsGap(16).rowsGap(16)
            }
            .layoutWeight(1).scrollBar(BarState.Auto)
          }
        }
        .layoutWeight(1).height('100%').padding({ left: 32 })

        Column() {
          this.DocDetail()
        }
        .width(480).height('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 16 })
      }
      .layoutWeight(1)
    }
    .width('100%').height('100%')

    if (this.showNewDoc) {
      this.NewDocDialog()
    }
  }

  @Builder
  Chip(label: string, active: boolean, onTap: () => void) {
    Text(label)
      .fontSize(13).fontColor(active ? C.inverse : C.textS)
      .height(32).padding({ left: 12, right: 12 })
      .borderRadius(16)
      .backgroundColor(active ? C.primary : C.surface2)
      .margin({ right: 8 })
      .onClick(() => onTap())
  }

  @Builder
  SyncDot(source: string) {
    Row() {
      Circle().width(8).height(8)
        .fill(source === 'cloud' ? C.success : C.localOnly)
      Text(source === 'cloud' ? '已同步' : '仅本地')
        .fontSize(11).fontColor(source === 'cloud' ? C.success : C.textT)
        .margin({ left: 4 })
    }
  }

  @Builder
  DocCard(d: KbDoc) {
    Column() {
      Row() {
        Text(d.title)
          .fontSize(18).fontWeight(500).fontColor(C.textP)
          .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
        if (d.favorite === 1) {
          Text('♥').fontSize(14).fontColor(C.accent).margin({ left: 8 })
        }
        this.SyncDot(d.source)
      }
      .width('100%')

      Text(d.summary.length > 0 ? d.summary : '(无摘要)')
        .fontSize(14).fontColor(C.textS).maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .margin({ top: 8 })

      Row() {
        // 固定 3 个条件 Text 替代嵌套 ForEach（规避 OHOS 6.1 stateMgmt
        // 在 @Builder×ForEach 嵌套重渲染时的 "undefined is not callable" 崩溃）
        if (this.docTags(d).length > 0) {
          Text(this.docTags(d)[0])
            .fontSize(11).fontColor(C.primary)
            .padding({ left: 8, right: 8 }).height(22)
            .backgroundColor(C.primaryContainer).borderRadius(11)
            .margin({ right: 6 })
        }
        if (this.docTags(d).length > 1) {
          Text(this.docTags(d)[1])
            .fontSize(11).fontColor(C.primary)
            .padding({ left: 8, right: 8 }).height(22)
            .backgroundColor(C.primaryContainer).borderRadius(11)
            .margin({ right: 6 })
        }
        if (this.docTags(d).length > 2) {
          Text(this.docTags(d)[2])
            .fontSize(11).fontColor(C.primary)
            .padding({ left: 8, right: 8 }).height(22)
            .backgroundColor(C.primaryContainer).borderRadius(11)
            .margin({ right: 6 })
        }
        Blank()
        Text(fmtTime(d.updated_at)).fontSize(11).fontColor(C.textT)
      }
      .width('100%').margin({ top: 10 })
    }
    .padding(16).borderRadius(16).backgroundColor(C.surface)
    .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })
    .constraintSize({ minHeight: 148 })
    .alignItems(HorizontalAlign.Start)
    .onClick(() => this.openDoc(d.id))
  }

  @Builder
  HitCard(h: SearchHit) {
    Column() {
      Row() {
        Text(h.title)
          .fontSize(16).fontWeight(500).fontColor(C.textP)
          .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
        if (this.searchMode === 'semantic') {
          Text(`${Math.round(h.score * 100)}%`)
            .fontSize(12).fontColor(C.primary).margin({ left: 8 })
        }
      }.width('100%')
      if (h.head.length > 0) {
        Text(`§ ${h.head}`)
          .fontSize(12).fontColor(C.primary).maxLines(1).margin({ top: 4 })
      }
      Text(h.snippet).fontSize(13).fontColor(C.textS).maxLines(3)
        .textOverflow({ overflow: TextOverflow.Ellipsis }).margin({ top: 6 })
    }
    .padding(16).borderRadius(12).backgroundColor(C.surface)
    .margin({ bottom: 12 }).alignItems(HorizontalAlign.Start)
    .onClick(() => this.openDoc(h.doc_id))
  }

  @Builder
  DocDetail() {
    if (this.curDoc === null) {
      Column() {
        Text('📄').fontSize(48)
        Text('选择一篇文档查看详情')
          .fontSize(14).fontColor(C.textT).margin({ top: 12 })
      }
      .width('100%').layoutWeight(1).justifyContent(FlexAlign.Center)
    } else {
      Scroll() {
        Column() {
          Text(this.curDoc.title)
            .fontSize(20).fontWeight(500).fontColor(C.textP)
          Row() {
            this.SyncDot(this.curDoc.source)
            Text(` · ${this.curDoc.category.length > 0 ? this.curDoc.category : '未分类'} · ${fmtTime(this.curDoc.updated_at)}`)
              .fontSize(12).fontColor(C.textT)
          }.margin({ top: 8 })
          Divider().margin({ top: 12, bottom: 12 }).color(C.stroke1)

          Text((this.curDoc.content || '').slice(0, 4000))
            .fontSize(14).fontColor(C.textP)
            .lineHeight(24).width('100%')
          if ((this.curDoc.content || '').length > 4000) {
            Text('… (内容过长，已截断显示)')
              .fontSize(12).fontColor(C.textT).margin({ top: 8 })
          }

          Row() {
            ForEach(this.curDoc.tags || [], (t: string) => {
              this.Chip(t, false, (): void => {})
            }, (t: string) => t)
          }.margin({ top: 12 })

          Divider().margin({ top: 16, bottom: 12 }).color(C.stroke1)
          Text(`笔记 (${(this.curDoc.notes || []).length})`)
            .fontSize(14).fontWeight(500).fontColor(C.textP)
          ForEach((this.curDoc.notes || []).slice(0, 5), (n: KbNote) => {
            Column() {
              if (n.quote.length > 0) {
                Row() {
                  Divider().vertical(true).height(40).width(3).color(C.primary)
                  Text(n.quote)
                    .fontSize(12).fontColor(C.textS).maxLines(2)
                    .margin({ left: 8 }).layoutWeight(1)
                }.alignItems(VerticalAlign.Top)
              }
              Text(n.note).fontSize(13).fontColor(C.textP).margin({ top: 4 })
            }
            .width('100%').padding(10).borderRadius(8)
            .backgroundColor(C.surface3).margin({ top: 8 })
            .alignItems(HorizontalAlign.Start)
          }, (n: KbNote) => n.id)

          TextArea({ placeholder: '写一条批注…', text: this.noteEdit })
            .onChange((v: string) => {
              this.noteEdit = v;
            })
            .height(72).fontSize(13).margin({ top: 12 })
            .backgroundColor(C.surface2).borderRadius(8)

          Row() {
            Button('加为笔记')
              .fontSize(13).height(34).borderRadius(17)
              .backgroundColor(C.primary).fontColor(C.inverse)
              .onClick(() => this.addNoteFor(this.curDoc !== null ? this.curDoc.id : ''))
            Blank()
            Button(this.curDoc.favorite === 1 ? '♥ 已收藏' : '♡ 收藏')
              .fontSize(13).height(34).borderRadius(17)
              .backgroundColor(C.surface).fontColor(C.accent)
              .border({ width: 1, color: C.stroke2 })
              .onClick(() => this.toggleFav(this.curDoc !== null ? this.curDoc.id : ''))
          }
          .width('100%').margin({ top: 10 })

          Button('⟳ 立即同步')
            .fontSize(13).height(34).borderRadius(17).width('100%')
            .backgroundColor(C.surface).fontColor(C.primary)
            .border({ width: 1, color: C.stroke2 }).margin({ top: 8 })
            .onClick(() => this.doSync())
        }
        .width('100%').padding(24).borderRadius(16)
        .backgroundColor(C.surface)
        .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })
        .alignItems(HorizontalAlign.Start)
      }
      .height('100%').scrollBar(BarState.Auto)
    }
  }

  @Builder
  NewDocDialog() {
    Column() {
      Column() {
        Text('新建文档').fontSize(20).fontWeight(500).fontColor(C.textP)
        TextInput({ placeholder: '标题（必填）' })
          .onChange((v: string) => {
            this.ndTitle = v;
          })
          .height(40).fontSize(14).backgroundColor(C.surface2).borderRadius(8).margin({ top: 16 })
        Row() {
          TextInput({ placeholder: '分类，如：移植' })
            .onChange((v: string) => {
              this.ndCat = v;
            })
            .height(40).fontSize(14).backgroundColor(C.surface2).borderRadius(8).layoutWeight(1)
          TextInput({ placeholder: '标签，逗号分隔' })
            .onChange((v: string) => {
              this.ndTags = v;
            })
            .height(40).fontSize(14).backgroundColor(C.surface2).borderRadius(8)
            .layoutWeight(1).margin({ left: 12 })
        }.margin({ top: 12 })
        TextArea({ placeholder: '正文（支持 Markdown）' })
          .onChange((v: string) => {
            this.ndContent = v;
          })
          .height(240).fontSize(14).backgroundColor(C.surface2).borderRadius(8).margin({ top: 12 })
        Row() {
          Blank()
          Button('取消')
            .fontSize(14).height(40).borderRadius(20)
            .backgroundColor(C.surface).fontColor(C.textS)
            .border({ width: 1, color: C.stroke2 }).margin({ right: 12 })
            .onClick(() => {
              this.showNewDoc = false;
            })
          Button('创建')
            .fontSize(14).height(40).borderRadius(20)
            .backgroundColor(C.primary).fontColor(C.inverse)
            .onClick(() => this.createDoc())
        }.width('100%').margin({ top: 20 })
      }
      .width(640).padding(24).borderRadius(24).backgroundColor(C.surface)
      .shadow({ radius: 32, offsetY: 12, color: 'rgba(23,35,54,0.12)' })
    }
    .width('100%').height('100%')
    .justifyContent(FlexAlign.Center)
    .backgroundColor('rgba(15,20,31,0.45)')
    .onClick(() => {
      this.showNewDoc = false;
    })
  }

  @Builder
  SkeletonGrid() {
    Column() {
      ForEach([0, 1, 2], (i: number) => {
        Row() {
          this.SkelCard()
          this.SkelCard()
        }.width('100%').margin({ bottom: 16 })
      }, (i: number) => 'sk' + i)
    }
    .width('100%').padding({ top: 8 })
  }

  @Builder
  SkelCard() {
    Column() {
      Row().width('60%').height(16).borderRadius(4).backgroundColor(C.stroke1)
      Row().width('100%').height(12).borderRadius(4).backgroundColor(C.stroke1).margin({ top: 12 })
      Row().width('70%').height(12).borderRadius(4).backgroundColor(C.stroke1).margin({ top: 8 })
    }
    .layoutWeight(1).padding(16).borderRadius(16).backgroundColor(C.surface)
    .margin({ left: 8, right: 8 }).constraintSize({ minHeight: 148 })
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  EmptyState(title: string, desc: string, btn: string, onTap: () => void) {
    Column() {
      Text('🗂️').fontSize(64)
      Text(title).fontSize(16).fontWeight(500).fontColor(C.textP).margin({ top: 16 })
      Text(desc).fontSize(14).fontColor(C.textS).margin({ top: 8 })
      Button(btn)
        .fontSize(14).height(40).borderRadius(20)
        .backgroundColor(C.primary).fontColor(C.inverse).margin({ top: 24 })
        .onClick(() => onTap())
    }
    .width('100%').layoutWeight(1).justifyContent(FlexAlign.Center)
  }

  // =========================================================
  // Tab2 AI 深研 (§6.2 三栏)
  // =========================================================
  @Builder
  PageResearch() {
    Row() {
      Column() {
        Text('研究引擎').fontSize(14).fontWeight(500).fontColor(C.textP)
          .margin({ bottom: 8 })
        ForEach(this.agentOptions, (a: string) => {
          Row() {
            Text(a === 'research' ? '🔬' :
              a === 'kb' ? '📚' :
              a === 'career' ? '🎮' :
              a === 'diag' ? '🩺' : '⚡').fontSize(18)
            Column() {
              Text(a === 'research' ? '深度研究' :
                a === 'kb' ? '知识库问答' :
                a === 'career' ? '职场模拟' :
                a === 'diag' ? '应用诊断' : '性能体检').fontSize(14)
                .fontColor(this.agentKind === a ? C.primary : C.textP)
              Text(a === 'research' ? 'DeepSearch 工作流' :
                a === 'kb' ? 'ReAct + 混合检索' :
                a === 'career' ? 'career-emulator' :
                a === 'diag' ? 'hilog 分析' : 'SP/实时指标').fontSize(11).fontColor(C.textT)
            }
            .alignItems(HorizontalAlign.Start).margin({ left: 10 })
          }
          .width('100%').height(56).padding({ left: 12, right: 12 })
          .borderRadius(12)
          .backgroundColor(this.agentKind === a ? C.primaryContainer : Color.Transparent)
          .onClick(() => {
            this.agentKind = a;
          })
        }, (a: string) => a)
        Blank()
        Text('事件流来自 sidecar\n每 2s 轮询 /api/task')
          .fontSize(10).fontColor(C.textT).margin({ bottom: 12 })
      }
      .width(280).height('100%').padding(16)

      Column() {
        Scroll() {
          Column() {
            if (this.rQuery.length > 0) {
              Row() {
                Blank()
                Text(this.rQuery)
                  .fontSize(15).fontColor(C.textP)
                  .padding(16).borderRadius(16)
                  .backgroundColor(C.primaryContainer)
                  .constraintSize({ maxWidth: 720 })
              }.width('100%').margin({ bottom: 16 })
            }

            Column() {
              Text(this.rState === 'running' ? '◐ 研究运行中' :
                this.rState === 'done' ? '✓ 研究完成' :
                  this.rState === 'error' ? '⚠ 运行出错' : '○ 待启动')
                .fontSize(14).fontWeight(500)
                .fontColor(this.rState === 'error' ? C.error :
                  this.rState === 'done' ? C.success : C.primary)
              if (this.rState === 'running') {
                Text(`${this.rElapsed}s`).fontSize(12).fontColor(C.textT).margin({ top: 2 })
              }
              ForEach(this.rEvents.slice(-40), (e: TaskEv, i: number) => {
                Row() {
                  Column() {
                    if (e.type === 'node' || e.type === 'done') {
                      Circle().width(8).height(8)
                        .fill(e.type === 'done' ? C.success : C.primary)
                    } else {
                      Circle().width(6).height(6).fill(C.stroke2)
                    }
                  }.width(20).alignItems(HorizontalAlign.Center)
                  Column() {
                    Text(evLabel(e))
                      .fontSize(13).fontWeight(500)
                      .fontColor(e.type === 'tool_error' ? C.error : C.textP)
                    if (e.head !== undefined && e.head.length > 0) {
                      Text(e.head).fontSize(11).fontColor(C.textT).maxLines(2)
                        .textOverflow({ overflow: TextOverflow.Ellipsis })
                    }
                  }
                  .alignItems(HorizontalAlign.Start).layoutWeight(1).margin({ left: 8 })
                }
                .width('100%').constraintSize({ minHeight: 32 })
                .alignItems(VerticalAlign.Top).margin({ top: 4 })
              }, (e: TaskEv, i: number) => `e${i}`)

              if (this.rState === 'running' && this.rEvents.length === 0) {
                Row() {
                  LoadingProgress().width(20).height(20).color(C.primary)
                  Text('正在初始化 agent…')
                    .fontSize(13).fontColor(C.textT).margin({ left: 8 })
                }.margin({ top: 12 })
              }
              if (this.rState === 'error' && this.rError.length > 0) {
                Text(this.rError).fontSize(12).fontColor(C.error).margin({ top: 8 })
              }
            }
            .width('100%').padding(20).borderRadius(16)
            .backgroundColor(C.surface).constraintSize({ minHeight: 240 })
            .alignItems(HorizontalAlign.Start)
            .margin({ bottom: 16 })
            .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })

            if (this.rResult.length > 0) {
              Column() {
                Text('研究报告').fontSize(24).fontWeight(500).fontColor(C.textP)
                  .margin({ bottom: 16 })
                this.Markdown()
                Row() {
                  Button('保存为知识库文档')
                    .fontSize(13).height(36).borderRadius(18)
                    .backgroundColor(C.primary).fontColor(C.inverse)
                    .onClick(() => this.saveReportAsDoc())
                  Button('提示')
                    .fontSize(13).height(36).borderRadius(18)
                    .backgroundColor(C.surface).fontColor(C.primary)
                    .border({ width: 1, color: C.stroke2 }).margin({ left: 12 })
                    .onClick(() => {
                      toast('使用"保存为知识库文档"归档报告');
                    })
                }.margin({ top: 16 })
              }
              .width('100%').padding(24).borderRadius(16)
              .backgroundColor(C.surface)
              .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })
              .alignItems(HorizontalAlign.Start)
              .constraintSize({ maxWidth: 1136 })
            }
          }
          .width('100%').padding(16)
        }
        .layoutWeight(1).scrollBar(BarState.Auto)

        Row() {
          TextArea({ placeholder: '输入研究问题，如：K3 芯片在 OHOS 生态中的定位？' })
            .onChange((v: string) => {
              this.rQuery = v;
            })
            .layoutWeight(1).height(64).fontSize(14)
            .backgroundColor(C.surface2).borderRadius(12)
          if (this.rState === 'running') {
            LoadingProgress().width(28).height(28).color(C.primary).margin({ left: 12 })
          } else {
            Button('发送 ➤')
              .fontSize(15).height(40).borderRadius(20)
              .backgroundColor(C.primary).fontColor(C.inverse).margin({ left: 12 })
              .padding({ left: 20, right: 20 })
              .onClick(() => this.startResearch())
          }
        }
        .width('100%').height(96).padding({ left: 16, right: 16, bottom: 16 })
      }
      .layoutWeight(1).height('100%')

      Column() {
        Text('引用来源').fontSize(14).fontWeight(500).fontColor(C.textP)
          .margin({ bottom: 8 })
        if (this.citations.length === 0) {
          Text('提交研究后，本地知识库的高相关片段将在此列出。')
            .fontSize(12).fontColor(C.textT)
        }
        Scroll() {
          Column() {
            ForEach(this.citations, (h: SearchHit, i: number) => {
              Column() {
                Row() {
                  Text(`${i + 1}`)
                    .fontSize(11).fontColor(C.primary)
                    .width(16).height(16).textAlign(TextAlign.Center)
                    .backgroundColor(C.primaryContainer).borderRadius(4)
                  Text(h.title)
                    .fontSize(13).fontWeight(500).fontColor(C.textP)
                    .maxLines(1).margin({ left: 8 })
                    .textOverflow({ overflow: TextOverflow.Ellipsis }).layoutWeight(1)
                }.width('100%')
                Text(h.snippet).fontSize(11).fontColor(C.textS).maxLines(3)
                  .textOverflow({ overflow: TextOverflow.Ellipsis }).margin({ top: 6 })
                Row() {
                  Row() {
                    Row().width(Math.max(4, Math.round(h.score * 120))).height(4)
                      .borderRadius(2).backgroundColor(C.primary)
                    Row().layoutWeight(1).height(4)
                  }.width(120).height(4).margin({ right: 8 })
                  Text(`${Math.round(h.score * 100)}%`).fontSize(10).fontColor(C.textT)
                }.margin({ top: 8 })
              }
              .width('100%').padding(12).borderRadius(12).backgroundColor(C.surface)
              .margin({ bottom: 8 }).alignItems(HorizontalAlign.Start)
              .onClick(() => this.openDoc(h.doc_id))
            }, (h: SearchHit) => 'c' + h.doc_id)
          }.width('100%')
        }
        .layoutWeight(1).scrollBar(BarState.Auto)
      }
      .width(336).height('100%').padding(16)
    }
    .width('100%').height('100%')
  }

  // 轻量 Markdown 渲染 — 直读 this.rResult（@Builder 值参数不响应状态变化）
  @Builder
  Markdown() {
    Column() {
      ForEach(mdParse(this.rResult), (b: MdBlock, i: number) => {
        if (b.type === 'h1') {
          Text(b.text).fontSize(20).fontWeight(700).fontColor(C.textP)
            .margin({ top: 12, bottom: 8 })
        } else if (b.type === 'h2') {
          Text(b.text).fontSize(17).fontWeight(500).fontColor(C.textP)
            .margin({ top: 10, bottom: 6 })
        } else if (b.type === 'code') {
          Text(b.text).fontSize(12).fontColor('#333B45')
            .fontFamily('monospace').width('100%')
            .padding(12).borderRadius(8)
            .backgroundColor(C.codeBg)
            .margin({ top: 6, bottom: 6 })
        } else if (b.type === 'li') {
          Row() {
            Text('•').fontSize(14).fontColor(C.primary).margin({ right: 8 })
            Text(b.text).fontSize(14).fontColor(C.textP).layoutWeight(1).lineHeight(22)
          }.alignItems(VerticalAlign.Top).margin({ top: 2 })
        } else {
          Text(b.text).fontSize(14).fontColor(C.textP).lineHeight(22)
            .margin({ top: 4 }).width('100%')
        }
      }, (b: MdBlock, i: number) => `m${i}`)
    }
    .width('100%').constraintSize({ maxWidth: 720 })
    .alignItems(HorizontalAlign.Start)
  }

  // =========================================================
  // Tab3 笔记 (§6.3 三栏)
  // =========================================================
  @Builder
  PageNotes() {
    Row() {
      Column() {
        Text('标签').fontSize(14).fontWeight(500).fontColor(C.textP).margin({ bottom: 8 })
        this.Chip('全部', this.curTag === '', (): void => {
          this.curTag = '';
          this.loadNotes();
        })
        ForEach(this.tagsAll, (t: string) => {
          this.Chip(t, this.curTag === t, (): void => {
            this.curTag = t;
            this.loadNotes();
          })
        }, (t: string) => 'nt' + t)
      }
      .width(264).height('100%').padding(16)

      Column() {
        TextInput({ placeholder: '搜索笔记…', text: this.noteQ })
          .onChange((v: string) => {
            this.noteQ = v;
            this.loadNotes();
          })
          .height(40).fontSize(13).borderRadius(20)
          .backgroundColor(C.surface2).margin({ bottom: 12 })
        Scroll() {
          Column() {
            ForEach(this.notes, (n: KbNote) => {
              Column() {
                Text(n.note.length > 0 ? n.note : '(空笔记)')
                  .fontSize(14).fontWeight(500).fontColor(C.textP)
                  .maxLines(2).textOverflow({ overflow: TextOverflow.Ellipsis })
                Row() {
                    Text(n.quote.length > 0 ? `↩ ${n.quote.slice(0, 20)}…` : (n.doc_title !== undefined && n.doc_title.length > 0 ? n.doc_title.slice(0, 14) : '独立笔记'))
                    .fontSize(11).fontColor(C.textT).layoutWeight(1).maxLines(1)
                  Text(fmtTime(n.updated_at)).fontSize(11).fontColor(C.textT)
                }.width('100%').margin({ top: 6 })
              }
              .width('100%').padding(12).borderRadius(12)
              .backgroundColor(this.curNote !== null && this.curNote.id === n.id ? C.primaryContainer : C.surface)
              .margin({ bottom: 8 }).alignItems(HorizontalAlign.Start)
              .onClick(() => {
                this.curNote = n;
              })
            }, (n: KbNote) => n.id + n.updated_at)
          }.width('100%')
        }
        .layoutWeight(1).scrollBar(BarState.Auto)
      }
      .width(432).height('100%').padding(16)

      Column() {
        if (this.curNote === null) {
          this.EmptyState('还没有选中笔记', '在左侧选择一条笔记查看详情', '刷新', (): void => this.loadNotes())
        } else {
          Scroll() {
            Column() {
              Column() {
                Row() {
                  Divider().vertical(true).height(60).width(3).color(C.primary)
                  Column() {
                    Text(this.curNote.quote.length > 0 ? this.curNote.quote : '(无摘录)')
                      .fontSize(13).fontColor(C.textS).maxLines(6).lineHeight(20)
                    Text(this.curNote.doc_title !== undefined && this.curNote.doc_title.length > 0 ? `—— 来自《${this.curNote.doc_title}》` : `—— 来自文档 ${this.curNote.doc_id}`)
                      .fontSize(11).fontColor(C.primary).margin({ top: 6 })
                      .onClick(() => this.openDoc(this.curNote !== null ? this.curNote.doc_id : ''))
                  }
                  .alignItems(HorizontalAlign.Start).margin({ left: 12 }).layoutWeight(1)
                }
                .alignItems(VerticalAlign.Top).width('100%')
              }
              .width('100%').padding(16).borderRadius(12)
              .backgroundColor(C.surface3).alignItems(HorizontalAlign.Start)

              Text('我的备注').fontSize(16).fontWeight(500).fontColor(C.textP)
                .margin({ top: 20, bottom: 8 })
              Text(this.curNote.note).fontSize(15).fontColor(C.textP).lineHeight(24).width('100%')

              Row() {
                ForEach(this.curNote.tags || [], (t: string) => {
                  this.Chip(t, false, (): void => {})
                }, (t: string) => t)
                Blank()
                Text(`修改于 ${fmtTime(this.curNote.updated_at)}`)
                  .fontSize(11).fontColor(C.textT)
              }.width('100%').margin({ top: 16 })

              Row() {
                Button('删除笔记')
                  .fontSize(13).height(36).borderRadius(18)
                  .backgroundColor(C.error).fontColor(C.inverse)
                  .onClick(() => this.curNote !== null && this.delNote(this.curNote.id))
                Blank()
                Button('刷新')
                  .fontSize(13).height(36).borderRadius(18)
                  .backgroundColor(C.surface).fontColor(C.primary)
                  .border({ width: 1, color: C.stroke2 })
                  .onClick(() => this.loadNotes())
              }.width('100%').margin({ top: 24 })
            }
            .width('100%').padding(24)
            .alignItems(HorizontalAlign.Start)
          }
          .layoutWeight(1).scrollBar(BarState.Auto)
        }
      }
      .layoutWeight(1).height('100%').padding(16)
    }
    .width('100%').height('100%')
  }

  // =========================================================
  // Tab4 Agent 商店 (§6.4)
  // =========================================================
  @Builder
  PageStore() {
    Column() {
      Row() {
        Text('Agent 商店').fontSize(28).fontWeight(700).fontColor(C.textP)
        Blank()
        Button('⟳ 刷新')
          .fontSize(13).height(36).borderRadius(18)
          .backgroundColor(C.surface).fontColor(C.primary)
          .border({ width: 1, color: C.stroke2 })
          .onClick(() => {
            this.loadStore();
            toast('目录已刷新');
          })
      }
      .width('100%').height(64).padding({ left: 32, right: 32 })

      Row() {
        Row() {
          Circle().width(8).height(8).fill(C.success)
          Text(`${this.storeStats !== null ? this.storeStats.installed : 0}`)
            .fontSize(20).fontWeight(500).fontColor(C.textP).margin({ left: 8 })
          Text('已安装').fontSize(12).fontColor(C.textT).margin({ left: 4 })
        }.margin({ right: 40 }).onClick(() => {
          this.storeFilter = '已安装';
        })
        Row() {
          Circle().width(8).height(8).fill(C.primary)
          Text(`${this.storeStats !== null ? this.storeStats.cloud : 0}`)
            .fontSize(20).fontWeight(500).fontColor(C.textP).margin({ left: 8 })
          Text('云端组件').fontSize(12).fontColor(C.textT).margin({ left: 4 })
        }.margin({ right: 40 }).onClick(() => {
          this.storeFilter = '云端';
        })
        Row() {
          Circle().width(8).height(8).fill(C.textT)
          Text(`${this.storeStats !== null ? this.storeStats.na : 0}`)
            .fontSize(20).fontWeight(500).fontColor(C.textP).margin({ left: 8 })
          Text('不适用').fontSize(12).fontColor(C.textT).margin({ left: 4 })
        }.onClick(() => {
          this.storeFilter = '不适用';
        })
        Blank()
        Text(`openJiuwen-ai 组织 ${this.storeStats !== null ? this.storeStats.total : 19} 组件`)
          .fontSize(12).fontColor(C.textT)
      }
      .width('100%').height(72).margin({ left: 32, right: 32 })
      .padding({ left: 24, right: 24 }).borderRadius(16).backgroundColor(C.surface)

      Row() {
        this.Chip('全部', this.storeFilter === '全部', (): void => {
          this.storeFilter = '全部';
        })
        this.Chip('已安装', this.storeFilter === '已安装', (): void => {
          this.storeFilter = '已安装';
        })
        this.Chip('云端', this.storeFilter === '云端', (): void => {
          this.storeFilter = '云端';
        })
        this.Chip('不适用', this.storeFilter === '不适用', (): void => {
          this.storeFilter = '不适用';
        })
      }
      .width('100%').height(44).padding({ left: 32, right: 32 })

      Scroll() {
        Grid() {
          ForEach(this.filteredStore(), (it: StoreItem) => {
            GridItem() {
              this.AgentCardUI(it)
            }
          }, (it: StoreItem) => it.id + it.enabled)
        }
        .columnsTemplate('1fr 1fr 1fr 1fr')
        .columnsGap(16).rowsGap(16)
      }
      .layoutWeight(1).scrollBar(BarState.Auto)
      .padding({ left: 32, right: 32, bottom: 16 })
    }
    .width('100%').height('100%')

    if (this.storeDetail !== null) {
      this.StoreSheet(this.storeDetail)
    }
  }

  filteredStore(): StoreItem[] {
    if (this.storeFilter === '全部') {
      return this.storeItems;
    }
    const m: Record<string, string> = {
      '已安装': 'installed', '云端': 'cloud', '不适用': 'na'
    };
    const want: string = m[this.storeFilter] !== undefined ? m[this.storeFilter] : '';
    return this.storeItems.filter((i: StoreItem): boolean => i.status === want);
  }

  @Builder
  AgentCardUI(it: StoreItem) {
    Column() {
      Row() {
        Text('📦').fontSize(20).width(40).height(40).textAlign(TextAlign.Center)
          .backgroundColor(C.surface2).borderRadius(12)
        Column() {
          Text(it.name).fontSize(18).fontWeight(500).fontColor(C.textP).maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Text(it.category).fontSize(11).fontColor(C.textT)
        }
        .alignItems(HorizontalAlign.Start).margin({ left: 12 }).layoutWeight(1)
        Row() {
          Circle().width(7).height(7)
            .fill(it.status === 'installed' ? C.success :
              it.status === 'cloud' ? C.primary : C.textT)
          Text(it.status === 'installed' ? '已安装' :
            it.status === 'cloud' ? '云端' : '不适用')
            .fontSize(10).margin({ left: 4 })
            .fontColor(it.status === 'installed' ? C.success :
              it.status === 'cloud' ? C.primary : C.textT)
        }
      }.width('100%')

      Text(it.desc).fontSize(13).fontColor(C.textS).maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis }).margin({ top: 8 })

      if (it.status !== 'installed') {
        Text(it.deps).fontSize(10).fontColor(C.textT).maxLines(1).margin({ top: 4 })
      }

      Row() {
        Text(it.run_count > 0 ? `已运行 ${it.run_count} 次` : (it.repo.split('/').pop() || ''))
          .fontSize(10).fontColor(C.textT).layoutWeight(1)
        if (it.runnable && it.enabled) {
          Button('运行')
            .fontSize(12).height(30).borderRadius(15)
            .backgroundColor(C.primary).fontColor(C.inverse)
            .padding({ left: 14, right: 14 })
            .onClick(() => this.runAgent(it))
        }
        if (it.runnable) {
          Text(it.enabled ? '禁用' : '启用').fontSize(12)
            .fontColor(it.enabled ? C.textS : C.primary)
            .padding({ left: 12, right: 4 })
            .onClick(() => this.toggleAgent(it))
        }
      }.width('100%').margin({ top: 10 })
    }
    .padding(16).borderRadius(16).backgroundColor(C.surface)
    .constraintSize({ minHeight: 168 })
    .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })
    .alignItems(HorizontalAlign.Start)
    .onClick(() => {
      this.storeDetail = it;
    })
  }

  @Builder
  StoreSheet(it: StoreItem) {
    Column() {
      Column() {
        Scroll() {
          Column() {
            Row() {
              Text('📦').fontSize(32)
              Column() {
                Text(it.name).fontSize(22).fontWeight(500).fontColor(C.textP)
                Text(`${it.category} · ${it.repo}`)
                  .fontSize(12).fontColor(C.textT).margin({ top: 4 })
              }.alignItems(HorizontalAlign.Start).margin({ left: 16 })
            }.width('100%')

            this.SheetRow('状态', it.status === 'installed' ? '已安装（本机可用）' :
              it.status === 'cloud' ? '云端组件（服务端部署）' : '不适用（依赖不满足）')
            this.SheetRow('描述', it.desc)
            this.SheetRow('依赖', it.deps)
            this.SheetRow('能力', (it.capabilities || []).join(' · '))
            this.SheetRow('运行统计', it.run_count > 0 ?
              `${it.run_count} 次 · 最近 ${fmtTime(it.last_run_at)}` : '尚未运行')
            if (it.status === 'na') {
              Text('该组件在本机不可用的原因见"依赖"一行；如需评估移植可行性，请在深研中提问。')
                .fontSize(12).fontColor(C.textT).margin({ top: 12 })
            }
          }
          .width('100%').padding(32).alignItems(HorizontalAlign.Start)
        }
        .layoutWeight(1)

        Row() {
          if (it.runnable && it.enabled) {
            Button('▶ 运行')
              .fontSize(14).height(40).borderRadius(20)
              .backgroundColor(C.primary).fontColor(C.inverse)
              .padding({ left: 24, right: 24 })
              .onClick(() => {
                this.storeDetail = null;
                this.runAgent(it);
              })
          }
          Blank()
          Button('关闭')
            .fontSize(14).height(40).borderRadius(20)
            .backgroundColor(C.surface).fontColor(C.textS)
            .border({ width: 1, color: C.stroke2 })
            .onClick(() => {
              this.storeDetail = null;
            })
        }.width('100%').padding({ left: 32, right: 32, bottom: 24 })
      }
      .width('100%').height(720)
      .borderRadius({ topLeft: 24, topRight: 24 })
      .backgroundColor(C.surface)
      .shadow({ radius: 32, offsetY: 12, color: 'rgba(23,35,54,0.12)' })
    }
    .width('100%').height('100%').justifyContent(FlexAlign.End)
    .backgroundColor('rgba(15,20,31,0.45)')
    .onClick(() => {
      this.storeDetail = null;
    })
  }

  @Builder
  SheetRow(k: string, v: string) {
    Row() {
      Text(k).fontSize(13).fontColor(C.textT).width(80)
      Text(v).fontSize(14).fontColor(C.textP).layoutWeight(1)
    }.width('100%').margin({ top: 16 }).alignItems(VerticalAlign.Top)
  }

  // =========================================================
  // Tab5 我的 · 端云协同 (§6.5)
  // =========================================================
  @Builder
  PageMe() {
    Row() {
      Column() {
        Column() {
          Text('端云协同').fontSize(18).fontWeight(500).fontColor(C.textP)
          Row() {
            Text('云端地址').fontSize(12).fontColor(C.textT)
            TextInput({ text: this.cloudUrlEdit })
              .onChange((v: string) => {
                this.cloudUrlEdit = v;
              })
              .layoutWeight(1).height(36).fontSize(12)
              .backgroundColor(C.surface2).borderRadius(8).margin({ left: 12 })
            Button('保存')
              .fontSize(12).height(32).borderRadius(16)
              .backgroundColor(C.surface).fontColor(C.primary)
              .border({ width: 1, color: C.stroke2 }).margin({ left: 8 })
              .onClick(() => this.saveCloudUrl())
          }.width('100%').margin({ top: 16 })

          Row() {
            if (this.syncing) {
              LoadingProgress().width(16).height(16).color(C.primary)
              Text('同步中…').fontSize(13).fontColor(C.primary).margin({ left: 8 })
            } else {
              Circle().width(8).height(8)
                .fill(this.sync !== null && this.sync.ok === false ? C.error :
                  this.sync !== null && this.sync.last_sync_at !== null ? C.success : C.localOnly)
              Text(this.sync !== null && this.sync.ok === false ? '上次同步失败' :
                this.sync !== null && this.sync.last_sync_at !== null ?
                  `上次同步 ${fmtTime(this.sync.last_sync_at)}` : '尚未同步')
                .fontSize(13).fontColor(C.textS).margin({ left: 8 })
            }
            Blank()
            if (this.sync !== null && this.sync.pending > 0) {
              Text(`待同步 ${this.sync.pending}`)
                .fontSize(12).fontColor(C.warning)
                .padding({ left: 8, right: 8 }).height(22)
                .backgroundColor(C.warningContainer).borderRadius(11)
            }
          }.width('100%').margin({ top: 16 })

          Row() {
            Button(this.syncing ? '同步中…' : '⟳ 立即同步')
              .fontSize(14).height(40).borderRadius(20)
              .backgroundColor(C.primary).fontColor(C.inverse)
              .padding({ left: 24, right: 24 })
              .onClick(() => this.doSync())
            Blank()
            Text(`设备 ${this.sync !== null ? this.sync.device_id : '-'}`)
              .fontSize(11).fontColor(C.textT)
          }.width('100%').margin({ top: 16 })
        }
        .width('100%').padding(24).borderRadius(16).backgroundColor(C.surface)
        .alignItems(HorizontalAlign.Start)
        .shadow({ radius: 8, offsetY: 2, color: 'rgba(23,35,54,0.06)' })

        Column() {
          Text('同步日志').fontSize(14).fontWeight(500).fontColor(C.textP).margin({ bottom: 8 })
          if (this.sync !== null && this.sync.logs !== null && this.sync.logs.length > 0) {
            Scroll() {
              Column() {
                ForEach(this.sync.logs, (l: SyncLog, i: number) => {
                  Row() {
                    Text(fmtTime(l.at)).fontSize(11).fontColor(C.textT).width(96)
                    Text(l.line).fontSize(11)
                      .fontColor(l.line.indexOf('error') >= 0 ? C.error : C.textS)
                      .layoutWeight(1).fontFamily('monospace')
                  }.width('100%').margin({ top: 4 })
                }, (l: SyncLog, i: number) => `l${i}l.at`)
              }.width('100%')
            }
            .layoutWeight(1).scrollBar(BarState.Auto)
          } else {
            Text('暂无日志').fontSize(12).fontColor(C.textT)
          }
        }
        .width('100%').layoutWeight(1).padding(24).borderRadius(16)
        .backgroundColor(C.surface).margin({ top: 16 })
        .alignItems(HorizontalAlign.Start)
      }
      .width(720).height('100%').padding({ left: 32, top: 16, bottom: 16, right: 16 })

      Column() {
        Row() {
          this.StatCell(`${this.stats !== null ? this.stats.docs : 0}`, '文档')
          this.StatCell(`${this.stats !== null ? this.stats.notes : 0}`, '笔记')
          this.StatCell(`${this.stats !== null ? this.stats.chunks : 0}`, '索引块')
          this.StatCell(`${this.stats !== null ? this.stats.categories : 0}`, '分类')
        }
        .width('100%').height(120)

        Column() {
          Row() {
            Text('长期记忆 (jiuwen_memory)').fontSize(14).fontWeight(500)
              .fontColor(C.textP).layoutWeight(1)
            Button('检索')
              .fontSize(12).height(32).borderRadius(16)
              .backgroundColor(C.primary).fontColor(C.inverse)
              .padding({ left: 14, right: 14 })
              .onClick(() => this.loadMems())
          }.width('100%').margin({ bottom: 8 })
          Row() {
            TextInput({ placeholder: '检索词', text: this.memQ })
              .onChange((v: string) => {
                this.memQ = v;
              })
              .layoutWeight(1).height(36).fontSize(13)
              .backgroundColor(C.surface2).borderRadius(8)
          }.width('100%').margin({ bottom: 8 })
          if (this.mems.length === 0) {
            Text('跨会话记忆：agent 的 remember 工具写入的事实会出现在这里。')
              .fontSize(12).fontColor(C.textT)
          }
          Scroll() {
            Column() {
              ForEach(this.mems, (m: MemItem, i: number) => {
                Row() {
                  Text(m.content !== undefined ? m.content : '')
                    .fontSize(12).fontColor(C.textS)
                    .layoutWeight(1).maxLines(2)
                  if (m.score !== undefined) {
                    Text(m.score.toFixed(2)).fontSize(10).fontColor(C.primary)
                      .margin({ left: 8 })
                  }
                }.width('100%').padding(8).borderRadius(8)
                .backgroundColor(C.surface2).margin({ top: 6 })
                .alignItems(VerticalAlign.Top)
              }, (m: MemItem, i: number) => `m${i}`)
            }.width('100%')
          }
          .layoutWeight(1).scrollBar(BarState.Auto)
        }
        .width('100%').layoutWeight(1).padding(24).borderRadius(16)
        .backgroundColor(C.surface).margin({ top: 16 })
        .alignItems(HorizontalAlign.Start)

        Column() {
          Text('设置').fontSize(14).fontWeight(500).fontColor(C.textP).margin({ bottom: 12 })
          this.SheetRow('模型', 'glm-5.2 (rvcompute)')
          this.SheetRow('主题', '浅色 (暗色主题规划中)')
          this.SheetRow('Sidecar', `${BASE}`)
          this.SheetRow('版本', 'openJiuwen 知识库 v1.0 · Apache-2.0')
        }
        .width('100%').padding(24).borderRadius(16)
        .backgroundColor(C.surface).margin({ top: 16 })
        .alignItems(HorizontalAlign.Start)
      }
      .layoutWeight(1).height('100%')
      .padding({ left: 16, top: 16, bottom: 16, right: 32 })
    }
    .width('100%').height('100%')
  }

  @Builder
  StatCell(num: string, label: string) {
    Column() {
      Text(num).fontSize(24).fontWeight(500).fontColor(C.textP)
      Text(label).fontSize(12).fontColor(C.textT).margin({ top: 4 })
    }
    .layoutWeight(1).height(120).borderRadius(16).backgroundColor(C.surface)
    .margin({ left: 8, right: 8 }).justifyContent(FlexAlign.Center)
  }
}
