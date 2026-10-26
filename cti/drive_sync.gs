/**
 * miso internet sales 통화 대시보드 ↔ 구글 드라이브 녹취 폴더 연결
 *
 * 쓰는 법 (한 번만)
 *  1. script.google.com → 새 프로젝트 → 이 코드를 통째로 붙여넣기
 *  2. 아래 FOLDER_IDS 에 녹취 폴더 주소의 마지막 부분을 넣기(드라이브가 여러 개면 ['ID1', 'ID2'])
 *     (https://drive.google.com/drive/folders/여기부분)
 *  3. 위쪽 함수 목록에서 '설치' 선택 → 실행 → 권한 허용
 *  이후 5분마다 새 녹취를 대시보드로 보냅니다. 녹취 파일 자체는 드라이브에 그대로 있어요.
 *
 *  [동기화] 버튼을 쓰려면(선택): 오른쪽 위 '배포' → '새 배포' → 유형 '웹 앱',
 *  실행 '나', 액세스 '회사의 모든 사용자'(또는 '모든 사용자') → 배포 → 나온 웹 앱 URL 을
 *  대시보드 설정에 붙여넣기.
 *
 *  에이전트 폴더 안 '계약완료' 폴더에 넣은 녹취는 대시보드에 '계약'으로 표시돼요(옮겨도 돼요).
 *
 *  대시보드에서 '백업하고 비우기'를 누르면, 다음 실행 때 그 에이전트 폴더의 녹취를
 *  같은 폴더 안 '_백업/에이전트' 폴더로 옮깁니다(지우지 않음, 계속 재생 가능).
 */
const CTI_URL = '{{CTI_URL}}';
const CTI_TOKEN = '{{CTI_TOKEN}}';
const FOLDER_IDS = ['여기에_녹취_폴더_ID'];
const CONTRACT_DIR = '계약완료';  // 에이전트 폴더 안 이 폴더의 녹취 = 계약 완료 건

const AUDIO = /\.(m4a|mp4|3gp|3ga|aac|mp3|wav|amr|ogg|opus|wma)$/i;
const BATCH = 200;              // 한 번에 보내는 파일 수
const TIME_BUDGET_MS = 270000;  // 실행 시간 제한(6분) 전에 끊고 다음 회차에 이어서
const BACKUP_DIR = '_백업';      // 이름이 '_'로 시작하는 폴더는 에이전트 폴더로 보지 않음

function 설치() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('동기화').timeBased().everyMinutes(5).create();
  PropertiesService.getScriptProperties().deleteProperty('since');
  PropertiesService.getScriptProperties().deleteProperty('before');
  roots_().forEach(function (id) { DriveApp.getFolderById(id); }); // 폴더 접근 권한 확인
  동기화();
}

/** 처음부터 다시 보내기(이미 있는 녹취는 대시보드가 알아서 건너뜀) */
function 전체다시보내기() {
  PropertiesService.getScriptProperties().deleteProperty('since');
  PropertiesService.getScriptProperties().deleteProperty('before');
  동기화();
}

/**
 * 5분마다 자동 실행 + 대시보드 [동기화] 버튼(웹 앱)에서도 불림.
 * budgetMs: 이번 실행에 쓸 시간(버튼은 짧게, 자동 실행은 길게).
 */
function 동기화(budgetMs) {
  const budget = typeof budgetMs === 'number' ? budgetMs : TIME_BUDGET_MS;  // 자동 실행은 이벤트 객체가 들어옴
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) { console.log('이미 동기화 중이라 건너뜀'); return { busy: true }; }
  try {
    const started = Date.now();
    const result = { added: 0, backups: 0 };
    // 1) 대시보드에서 누른 백업 요청부터 처리(오래 기다리지 않게)
    try { result.backups = runBackups_(started, Math.min(BACKUP_BUDGET_MS, budget * 0.6)); }
    catch (e) { console.error('백업 처리 중 오류: ' + e); }
    // 2) 새 녹취 보내기
    let s;
    try {
      s = syncNew_(started, budget);
    } catch (e) {
      report_({ ok: false, error: String(e).slice(0, 300) });  // 대시보드 설정 화면에 '오류'로 보이게
      throw e;
    }
    result.added = s.added; result.sent = s.sent; result.more = s.more;
    report_({ ok: true, added: result.added, found: s.sent, more: result.more });
    return result;
  } finally {
    lock.releaseLock();
  }
}

/**
 * 대시보드 [동기화] 버튼이 부르는 곳(웹 앱으로 배포했을 때). 두 가지 방식:
 *  - 대시보드 서버가 연동 키(token)로 부름 → JSON (액세스 '모든 사용자' 배포)
 *  - 회사 계정으로 로그인한 브라우저가 새 탭으로 엶 → 결과 화면 (액세스 '회사의 모든 사용자' 배포)
 */
function doGet(e) {
  const p = (e && e.parameter) || {};
  const byServer = !!p.token;
  if (byServer && p.token !== CTI_TOKEN) return json_({ ok: false, error: '연동 키가 맞지 않아요' });
  if (!byServer && !sameDomainUser_()) return page_('동기화할 수 없어요', '회사 구글 계정으로 로그인한 브라우저에서 눌러 주세요.', false, {});
  let r;
  try {
    r = 동기화(byServer ? SYNC_NOW_BUDGET_MS : SYNC_TAB_BUDGET_MS);
  } catch (err) {
    return byServer ? json_({ ok: false, error: String(err).slice(0, 300) })
      : page_('동기화 중 문제가 생겼어요', String(err).slice(0, 300), false, {});
  }
  if (r.busy) {
    return byServer ? json_({ ok: false, busy: true, error: '이미 동기화 중이에요. 잠시 후 다시 눌러 주세요.' })
      : page_('이미 동기화 중이에요', '잠시 후 다시 눌러 주세요.', false, { busy: true });
  }
  if (byServer) return json_(Object.assign({ ok: true }, r));
  const lines = [r.added ? '새 녹취 ' + r.added + '건을 가져왔어요.' : '새 녹취가 없어요.'];
  if (r.backups) lines.push('백업 ' + r.backups + '건을 처리했어요.');
  if (r.more) lines.push('남은 녹취는 곧 이어서 가져와요.');
  return page_('동기화했어요', lines.join(' '), true, r);
}

/** 웹 앱을 연 사람이 스크립트 주인과 같은 회사 도메인 계정인지 */
function sameDomainUser_() {
  const me = Session.getActiveUser().getEmail();
  const owner = Session.getEffectiveUser().getEmail();
  return !!me && !!owner && me.split('@')[1] === owner.split('@')[1];
}

/**
 * 새 탭 결과 화면. 대시보드가 연 탭이면 결과를 대시보드에 알림으로 보내고 탭을 스스로 닫는다.
 * (닫히지 않으면 화면의 버튼으로 돌아가면 됨)
 */
function page_(title, body, ok, data) {
  const esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  const html = '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<div style="font-family:system-ui,-apple-system,sans-serif;max-width:420px;margin:15vh auto;padding:24px;text-align:center">' +
    '<div style="font-size:44px">' + (ok ? '✅' : '⚠️') + '</div>' +
    '<h2 style="margin:12px 0 8px;color:#191f28">' + esc(title) + '</h2>' +
    '<p style="color:#4e5968;font-size:16px;line-height:1.5">' + esc(body) + '</p>' +
    '<p style="color:#8b95a1;font-size:14px;margin-top:24px">이 탭을 닫고 대시보드로 돌아가면 목록이 새로 고쳐져요.</p>' +
    '<a href="' + esc(CTI_URL) + '/#calls" target="_top" style="display:inline-block;margin-top:8px;padding:14px 22px;border-radius:14px;' +
    'background:#055cf5;color:#fff;text-decoration:none;font-weight:700">대시보드로 돌아가기</a></div>' +
    '<script>try{var o=window.top.opener;if(o){o.postMessage(' +
    JSON.stringify({ type: 'cti-sync', ok: ok, title: title, message: body, added: (data && data.added) || 0 }) +
    ",'*');setTimeout(function(){window.top.close();},400);}}catch(e){}</script>";  // 결과(건수)만 담겨 있어 받는 곳 제한 불필요
  return HtmlService.createHtmlOutput(html).setTitle(title);
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

/**
 * 최신 녹취부터 보낸다.
 *  1) 지난 회차 이후 새로 올라온 녹취('since' 이후)
 *  2) 남는 시간에 지난 녹취를 최근 것부터 거꾸로('before' 이전, 다 채우면 'done')
 */
function syncNew_(started, budget) {
  const props = PropertiesService.getScriptProperties();
  let since = props.getProperty('since');
  let before = props.getProperty('before');
  const dirs = agentFolders_();
  // 폴더를 못 찾으면 '다 보냈음'으로 기록하지 않고 멈춤(대시보드 설정 화면에 오류로 보임)
  if (!dirs.length) throw new Error('녹취 폴더에서 에이전트 폴더를 찾지 못했어요. 스크립트의 FOLDER_IDS 와, 이 구글 계정이 드라이브 멤버인지 확인해 주세요.');
  const scopes = scopes_(dirs);
  const st = { sent: 0, added: 0, first: true, dirs: dirs };
  const timeLeft = function () { return Date.now() - started < budget; };

  if (!since || !before) {  // 처음: 지금부터 거꾸로 채우기 시작
    since = new Date(started - 10 * 60 * 1000).toISOString();
    before = new Date(started).toISOString();
    props.setProperties({ since: since, before: before });
  } else {
    // 1) 새 녹취(보통 몇 개 안 됨)
    const files = [];
    scopes.forEach(function (s) { newFiles_(s, since).forEach(function (x) { if (x.d) pushFile_(files, x.f, x.d); }); });
    files.sort(function (a, b) { return a.created < b.created ? -1 : 1; });
    let i = 0;
    while (i < files.length && timeLeft()) {
      const chunk = files.slice(i, i + BATCH);
      send_(chunk, st);
      i += chunk.length;
      props.setProperty('since', chunk[chunk.length - 1].created);
    }
    if (i >= files.length) props.setProperty('since', new Date(started - 10 * 60 * 1000).toISOString());  // 10분 겹치게(중복은 대시보드가 거름)
    else st.more = true;
  }

  // 계약완료 폴더로 옮긴 녹취는 생성 시각이 그대로라 위에서 안 잡힘 → 파일 ID 목록을 따로 알림.
  // 바로가기는 가리키는 원래 녹취에 표시하고, 예전에 통화로 잘못 들어간 바로가기는 지우게 알림
  const c = contractIds_(scopes);
  for (let i = 0; i < Math.max(c.ids.length, c.shortcuts.length); i += 1000) {
    post_({ files: [], first: false, contracts: c.ids.slice(i, i + 1000), shortcuts: c.shortcuts.slice(i, i + 1000) });
  }

  // 2) 지난 녹취를 최근 것부터
  while (before !== 'done' && timeLeft()) {
    // 드라이브마다 before 이전 최근 파일 BATCH 개(_백업 등 에이전트 폴더 밖 파일 포함) → 합쳐서 최근 BATCH 개
    const raw = [];
    let full = false;  // 어느 쪽이든 BATCH 개를 꽉 채워 왔으면 그 뒤에 같은 시각 파일이 더 있을 수 있음
    scopes.forEach(function (s) { const r = olderFiles_(s, before); full = full || r.length >= BATCH; r.forEach(function (x) { raw.push(x); }); });
    if (!raw.length) { before = 'done'; break; }
    raw.sort(function (a, b) { return a.f.createdTime > b.f.createdTime ? -1 : 1; });
    let n = Math.min(BATCH, raw.length);
    const last = raw[n - 1].f.createdTime;
    let next = last;
    if (full && raw[0].f.createdTime !== last) {
      // 경계의 같은 시각 파일은 이번에 안 보내고 다음 묶음에서 한꺼번에(before 를 1ms 뒤로)
      while (n > 0 && raw[n - 1].f.createdTime === last) n--;
      next = new Date(Date.parse(last) + 1).toISOString();
    } else {
      while (n < raw.length && raw[n].f.createdTime === last) n++;
    }
    const chunk = [];
    raw.slice(0, n).forEach(function (x) { if (x.d) pushFile_(chunk, x.f, x.d); });
    if (chunk.length) send_(chunk, st);
    before = next;
    props.setProperty('before', before);
  }
  props.setProperty('before', before);
  if (st.first) send_([], st);  // 보낼 게 없어도 폴더 목록·이번 회차 결과는 알림
  if (before !== 'done') st.more = true;

  console.log('보낸 파일 ' + st.sent + ' (새로 추가 ' + st.added + ')' + (before === 'done' ? ' · 지난 녹취 모두 보냄' : ' · 지난 녹취는 ' + before + ' 이전부터 이어서'));
  return { sent: st.sent, added: st.added, more: !!st.more };
}

function send_(chunk, st) {
  chunk.forEach(function (f) { if (f.durationMs == null) f.durationMs = mp4DurationMs_(f.id, f.name); });
  const body = { files: chunk, first: st.first };
  if (st.first) body.folders = st.dirs.map(function (d) { return d.name; });  // 녹취가 없는 에이전트 폴더도 알림
  st.added += post_(body).added || 0;
  st.first = false;
  st.sent += chunk.length;
}

const FILE_FIELDS = 'id,name,size,createdTime,parents,videoMediaMetadata(durationMillis),shortcutDetails(targetId)';
const NOT_FOLDER = "trashed = false and mimeType != 'application/vnd.google-apps.folder'";

/**
 * 녹취 폴더마다 {drive: 공유 드라이브 ID 또는 null, folders: {폴더 ID: {id, agent, contract}}}.
 * 공유 드라이브 맨 위 폴더면 드라이브 전체를 한 번에 검색(폴더 수천 개를 하나씩 열지 않게),
 * 일반 폴더면 하위 폴더를 하나씩 연다.
 */
function scopes_(dirs) {
  return roots_().map(function (root) {
    const mine = dirs.filter(function (d) { return d.root === root; });
    const folders = {};
    if (isSharedDrive_(root)) {
      return { drive: root, folders: driveFolderMap_(root, mine) };
    }
    folderTree_(mine).forEach(function (d) { folders[d.id] = d; });
    return { drive: null, folders: folders };
  });
}

function isSharedDrive_(id) {
  const res = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/drives/' + id + '?fields=id', {
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, muteHttpExceptions: true,
  });
  return res.getResponseCode() === 200;
}

/** 공유 드라이브의 모든 폴더를 한 번에 받아, 에이전트 폴더 아래 폴더마다 에이전트·계약완료 여부를 붙임 */
function driveFolderMap_(driveId, agentDirs) {
  const byId = {};
  driveList_("trashed = false and mimeType = 'application/vnd.google-apps.folder'", 'files(id,name,parents)', driveId)
    .forEach(function (f) { byId[f.id] = f; });
  const out = {};
  agentDirs.forEach(function (d) { out[d.id] = { id: d.id, agent: d.name, contract: false }; });
  const resolve = function (id) {
    if (out[id] !== undefined) return out[id];
    const f = byId[id];
    const up = f && f.parents ? resolve(f.parents[0]) : null;  // 드라이브 맨 위·'_' 폴더 쪽이면 null
    out[id] = up ? { id: id, agent: up.agent, contract: up.contract || f.name.trim() === CONTRACT_DIR } : null;
    return out[id];
  };
  Object.keys(byId).forEach(resolve);
  Object.keys(out).forEach(function (k) { if (!out[k]) delete out[k]; });
  return out;
}

/** since 이후 올라온 파일: [{f, d}] (d 가 null 이면 에이전트 폴더 밖) */
function newFiles_(s, since) {
  if (s.drive) {
    return driveList_(NOT_FOLDER + " and createdTime > '" + since + "'", 'files(' + FILE_FIELDS + ')', s.drive)
      .map(function (f) { return { f: f, d: s.folders[(f.parents || [])[0]] || null }; });
  }
  const out = [];
  Object.keys(s.folders).forEach(function (id) {
    listFiles_(id, since).forEach(function (f) { out.push({ f: f, d: s.folders[id] }); });
  });
  return out;
}

/** before 이전에 올라온 파일을 최근 것부터: 공유 드라이브는 BATCH 개, 일반 폴더는 폴더마다 BATCH 개 */
function olderFiles_(s, before) {
  const q = NOT_FOLDER + " and createdTime < '" + before + "'";
  if (s.drive) {
    return drivePage_(q, s.drive).map(function (f) { return { f: f, d: s.folders[(f.parents || [])[0]] || null }; });
  }
  const out = [];
  Object.keys(s.folders).forEach(function (id) {
    drivePage_("'" + id + "' in parents and " + q, null).forEach(function (f) { out.push({ f: f, d: s.folders[id] }); });
  });
  return out;
}

function drivePage_(q, driveId) {
  const url = 'https://www.googleapis.com/drive/v3/files?pageSize=' + BATCH + '&orderBy=createdTime%20desc' +
    '&supportsAllDrives=true&includeItemsFromAllDrives=true' + corpora_(driveId) + '&q=' + encodeURIComponent(q) +
    '&fields=' + encodeURIComponent('files(' + FILE_FIELDS + ')');
  const res = UrlFetchApp.fetch(url, { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() } });
  return JSON.parse(res.getContentText()).files || [];
}

function corpora_(driveId) {
  return driveId ? '&corpora=drive&driveId=' + driveId : '&corpora=allDrives';
}

/** 에이전트 폴더와 그 아래 모든 폴더: {id, agent, contract} */
function folderTree_(dirs) {
  const out = [];
  const visit = function (id, agent, contract) {
    out.push({ id: id, agent: agent, contract: contract });
    listChildren_(id, true).forEach(function (sub) { visit(sub.id, agent, contract || sub.name.trim() === CONTRACT_DIR); });
  };
  dirs.forEach(function (d) { visit(d.id, d.name, false); });
  return out;
}

function pushFile_(out, f, d) {
  if (!AUDIO.test(f.name) || f.shortcutDetails) return;  // 바로가기는 통화가 아님(원래 녹취가 따로 있음)
  out.push({
    id: f.id, agent: d.agent, name: f.name, size: Number(f.size || 0), created: f.createdTime,
    durationMs: f.videoMediaMetadata && f.videoMediaMetadata.durationMillis
      ? Number(f.videoMediaMetadata.durationMillis) : null,
    contract: d.contract,
  });
}

function roots_() {
  return FOLDER_IDS.map(function (id) { return String(id).trim(); }).filter(String);
}

/** 각 녹취 폴더 바로 아래 폴더들(= 에이전트). root: 그 폴더가 있는 녹취 폴더 ID(백업 위치) */
function agentFolders_() {
  const out = [];
  roots_().forEach(function (root) {
    listChildren_(root, true).forEach(function (f) {
      if (f.name.charAt(0) !== '_') out.push({ id: f.id, name: f.name, root: root });
    });
  });
  return out;
}

/** 계약완료 폴더의 녹취 ID(바로가기면 가리키는 원래 녹취 ID)와 바로가기 자체의 ID */
function contractIds_(scopes) {
  const out = { ids: [], shortcuts: [] };
  scopes.forEach(function (s) {
    Object.keys(s.folders).forEach(function (id) {
      if (!s.folders[id].contract) return;
      listFiles_(id, null).forEach(function (f) {
        if (!AUDIO.test(f.name)) return;
        if (f.shortcutDetails) { out.ids.push(f.shortcutDetails.targetId); out.shortcuts.push(f.id); }
        else out.ids.push(f.id);
      });
    });
  });
  return out;
}

const BACKUP_BUDGET_MS = 200000;    // 백업에 쓰는 최대 시간(나머지는 새 녹취 보내기)
const SYNC_NOW_BUDGET_MS = 20000;   // [동기화] 버튼(서버 방식): 대시보드가 기다릴 수 있는 만큼만 하고, 나머지는 자동 실행이 이어서
const SYNC_TAB_BUDGET_MS = 60000;   // [동기화] 버튼(새 탭 방식): 브라우저는 조금 더 기다릴 수 있음

/** 대시보드에 쌓인 백업 요청 처리. 처리한 요청 수를 돌려준다. */
function runBackups_(started, budget) {
  const tasks = JSON.parse(call_('get', '/api/drive/tasks').getContentText()).tasks || [];
  if (tasks.length) console.log('백업 요청 ' + tasks.length + '건');
  let n = 0;
  tasks.forEach(function (t) {
    if (Date.now() - started > budget) return;  // 남은 건 다음 회차에
    n++;
    try {
      backupOne_(t, started, budget);
    } catch (e) {
      console.error(t.agent + ' 백업 실패: ' + e);
      try {
        call_('post', '/api/drive/tasks/' + t.id, { status: 'failed', moved_ids: [], message: String(e).slice(0, 200) });
      } catch (e2) { /* 다음 회차에 다시 */ }
    }
  });
  return n;
}

function backupOne_(t, started, budget) {
  // 같은 이름 에이전트 폴더가 여러 드라이브에 있으면 모두 처리
  const dirs = agentFolders_().filter(function (f) { return f.name === t.agent; });
  if (!dirs.length) {
    call_('post', '/api/drive/tasks/' + t.id, { status: 'done', moved_ids: [], message: '폴더가 없어서 옮길 파일이 없었어요' });
    return;
  }
  const moved = [];
  let failed = 0, lastError = null, done = true;
  dirs.forEach(function (dir) {
    if (!done) return;
    // 1) 옮기기 전에 이 폴더의 녹취를 대시보드에 적재(누락 방지 — 이미 있는 건 대시보드가 건너뜀).
    //    빠르게 하려고 길이는 드라이브가 알려 준 값만 쓴다(없으면 대시보드가 용량으로 추정).
    const files = [];
    walk_(dir.id, t.agent, null, files, false);
    for (let i = 0; i < files.length; i += BATCH) post_({ files: files.slice(i, i + BATCH), first: false });
    // 2) 그 드라이브의 _백업/에이전트 폴더로 옮기기
    const dest = ensureFolder_(ensureFolder_(DriveApp.getFolderById(dir.root), BACKUP_DIR), t.agent);
    for (let i = 0; i < files.length; i++) {
      if (Date.now() - started > budget) { done = false; break; }
      try { DriveApp.getFileById(files[i].id).moveTo(dest); moved.push(files[i].id); }
      catch (e) { failed++; lastError = String(e); }
    }
  });
  console.log(t.agent + ': ' + moved.length + '개 옮김' + (failed ? ', ' + failed + '개 실패' : '') + (done ? '' : ' (다음 회차에 이어서)'));
  call_('post', '/api/drive/tasks/' + t.id, {
    status: !done ? 'progress' : (failed && !moved.length ? 'failed' : 'done'),
    moved_ids: moved, failed: failed,
    message: lastError ? '옮기지 못한 파일 ' + failed + '개: ' + lastError.slice(0, 200) : null,
  });
}

function ensureFolder_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function call_(method, path, body) {
  const opt = { method: method, headers: { 'X-CTI-Token': CTI_TOKEN }, muteHttpExceptions: true };
  if (body) { opt.contentType = 'application/json'; opt.payload = JSON.stringify(body); }
  const res = UrlFetchApp.fetch(CTI_URL.replace(/\/$/, '') + path, opt);
  if (res.getResponseCode() !== 200) {
    throw new Error('대시보드 요청 실패 (' + res.getResponseCode() + '): ' + res.getContentText());
  }
  return res;
}

function walk_(folderId, agent, since, out, contract) {
  listFiles_(folderId, since).forEach(function (f) {
    if (!AUDIO.test(f.name) || f.shortcutDetails) return;
    out.push({
      id: f.id, agent: agent, name: f.name, size: Number(f.size || 0), created: f.createdTime,
      durationMs: f.videoMediaMetadata && f.videoMediaMetadata.durationMillis
        ? Number(f.videoMediaMetadata.durationMillis) : null,
      contract: contract,
    });
  });
  listChildren_(folderId, true).forEach(function (sub) {
    walk_(sub.id, agent, since, out, contract || sub.name.trim() === CONTRACT_DIR);
  });
}

function listChildren_(folderId, foldersOnly) {
  const q = "'" + folderId + "' in parents and trashed = false and mimeType " +
    (foldersOnly ? "=" : "!=") + " 'application/vnd.google-apps.folder'";
  return driveList_(q, 'files(id,name)');
}

function listFiles_(folderId, since) {
  let q = "'" + folderId + "' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'";
  if (since) q += " and createdTime > '" + since + "'";
  return driveList_(q, 'files(' + FILE_FIELDS + ')');
}

function driveList_(q, fields, driveId) {
  const out = [];
  let pageToken = '';
  do {
    const url = 'https://www.googleapis.com/drive/v3/files?pageSize=1000&supportsAllDrives=true' +
      '&includeItemsFromAllDrives=true' + corpora_(driveId) + '&q=' + encodeURIComponent(q) +
      '&fields=' + encodeURIComponent('nextPageToken,' + fields) + (pageToken ? '&pageToken=' + pageToken : '');
    const res = UrlFetchApp.fetch(url, { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() } });
    const body = JSON.parse(res.getContentText());
    (body.files || []).forEach(function (f) { out.push(f); });
    pageToken = body.nextPageToken || '';
  } while (pageToken);
  return out;
}

/** 드라이브가 길이를 안 알려 주는 m4a/mp4: 파일 앞·뒤 일부만 받아서 mvhd 상자에서 길이를 읽음 */
function mp4DurationMs_(id, name) {
  if (!/\.(m4a|mp4|3gp|3ga)$/i.test(name)) return null;
  const ranges = ['bytes=0-262143', 'bytes=-262144'];
  for (let i = 0; i < ranges.length; i++) {
    try {
      const res = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + id + '?alt=media&supportsAllDrives=true', {
        headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken(), Range: ranges[i] },
        muteHttpExceptions: true,
      });
      if (res.getResponseCode() >= 300) continue;
      const ms = parseMvhd_(res.getContent());
      if (ms != null) return ms;
    } catch (e) { /* 다음 방법으로 */ }
  }
  return null;
}

function parseMvhd_(b) {
  const u8 = function (i) { return b[i] & 0xff; };
  const u32 = function (i) { return ((u8(i) * 16777216) + (u8(i + 1) << 16) + (u8(i + 2) << 8) + u8(i + 3)); };
  for (let i = 0; i + 32 < b.length; i++) {
    if (u8(i) === 0x6d && u8(i + 1) === 0x76 && u8(i + 2) === 0x68 && u8(i + 3) === 0x64) { // 'mvhd'
      const v = u8(i + 4);
      const scale = v === 1 ? u32(i + 24) : u32(i + 16);
      const dur = v === 1 ? u32(i + 28) * 4294967296 + u32(i + 32) : u32(i + 20);
      return scale ? Math.round(dur * 1000 / scale) : null;
    }
  }
  return null;
}

/** 이번 회차 결과(성공·오류)를 대시보드에 알림 — 설정 화면의 연결 상태. 실패해도 동기화는 계속 */
function report_(body) {
  try { call_('post', '/api/drive/run', body); } catch (e) { console.error('결과 알리기 실패: ' + e); }
}

function post_(body) {
  return JSON.parse(call_('post', '/api/drive/push', body).getContentText());
}
