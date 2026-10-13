/**
 * miso internet sales 통화 대시보드 ↔ 구글 드라이브 녹취 폴더 연결
 *
 * 쓰는 법 (한 번만)
 *  1. script.google.com → 새 프로젝트 → 이 코드를 통째로 붙여넣기
 *  2. 아래 FOLDER_ID 에 녹취 폴더 주소의 마지막 부분을 넣기
 *     (https://drive.google.com/drive/folders/여기부분)
 *  3. 위쪽 함수 목록에서 '설치' 선택 → 실행 → 권한 허용
 *  이후 5분마다 새 녹취를 대시보드로 보냅니다. 녹취 파일 자체는 드라이브에 그대로 있어요.
 *
 *  [동기화] 버튼을 쓰려면(선택): 오른쪽 위 '배포' → '새 배포' → 유형 '웹 앱',
 *  실행 '나', 액세스 '회사의 모든 사용자'(또는 '모든 사용자') → 배포 → 나온 웹 앱 URL 을
 *  대시보드 설정에 붙여넣기.
 *
 *  대시보드에서 '백업하고 비우기'를 누르면, 다음 실행 때 그 상담원 폴더의 녹취를
 *  같은 폴더 안 '_백업/상담원' 폴더로 옮깁니다(지우지 않음, 계속 재생 가능).
 */
const CTI_URL = '{{CTI_URL}}';
const CTI_TOKEN = '{{CTI_TOKEN}}';
const FOLDER_ID = '여기에_녹취_폴더_ID';

const AUDIO = /\.(m4a|mp4|3gp|3ga|aac|mp3|wav|amr|ogg|opus|wma)$/i;
const BATCH = 200;              // 한 번에 보내는 파일 수
const TIME_BUDGET_MS = 270000;  // 실행 시간 제한(6분) 전에 끊고 다음 회차에 이어서
const BACKUP_DIR = '_백업';      // 이름이 '_'로 시작하는 폴더는 상담원 폴더로 보지 않음

function 설치() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('동기화').timeBased().everyMinutes(5).create();
  PropertiesService.getScriptProperties().deleteProperty('since');
  DriveApp.getFolderById(FOLDER_ID.trim()); // 폴더 접근 권한 확인
  동기화();
}

/** 처음부터 다시 보내기(이미 있는 녹취는 대시보드가 알아서 건너뜀) */
function 전체다시보내기() {
  PropertiesService.getScriptProperties().deleteProperty('since');
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
    result.added = s.added; result.found = s.found; result.sent = s.sent; result.more = s.sent < s.found;
    report_({ ok: true, added: result.added, found: result.found, more: result.more });
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

function syncNew_(started, budget) {
  const props = PropertiesService.getScriptProperties();
  const since = props.getProperty('since');
  const files = [];
  // 최상위 폴더 바로 아래 폴더 이름 = 상담원
  const dirs = agentFolders_();
  dirs.forEach(function (agentDir) {
    walk_(agentDir.id, agentDir.name, since, files);
  });
  files.sort(function (a, b) { return a.created < b.created ? -1 : 1; });

  let sent = 0, added = 0, newest = since, first = true;
  while (sent < files.length || first) {
    const chunk = files.slice(sent, sent + BATCH);
    chunk.forEach(function (f) { if (f.durationMs == null) f.durationMs = mp4DurationMs_(f.id, f.name); });
    const body = { files: chunk, first: first };
    if (first) body.folders = dirs.map(function (d) { return d.name; });  // 녹취가 없는 상담원 폴더도 알림
    added += post_(body).added || 0;
    first = false;
    sent += chunk.length;
    if (chunk.length) newest = chunk[chunk.length - 1].created;
    if (!chunk.length || Date.now() - started > budget) break;
  }
  // 다음 회차는 이번에 보낸 마지막 파일 이후부터(10분 겹치게 — 중복은 대시보드가 걸러냄)
  if (sent >= files.length) {
    props.setProperty('since', new Date(started - 10 * 60 * 1000).toISOString());
  } else if (newest) {
    props.setProperty('since', newest);
  }
  console.log('보낸 파일 ' + sent + ' / 찾은 파일 ' + files.length + ' (새로 추가 ' + added + ')');
  return { sent: sent, found: files.length, added: added };
}

function agentFolders_() {
  return listChildren_(FOLDER_ID.trim(), true).filter(function (f) { return f.name.charAt(0) !== '_'; });
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
  const dir = agentFolders_().filter(function (f) { return f.name === t.agent; })[0];
  if (!dir) {
    call_('post', '/api/drive/tasks/' + t.id, { status: 'done', moved_ids: [], message: '폴더가 없어서 옮길 파일이 없었어요' });
    return;
  }
  // 1) 옮기기 전에 이 폴더의 녹취를 대시보드에 적재(누락 방지 — 이미 있는 건 대시보드가 건너뜀).
  //    빠르게 하려고 길이는 드라이브가 알려 준 값만 쓴다(없으면 대시보드가 용량으로 추정).
  const files = [];
  walk_(dir.id, t.agent, null, files);
  for (let i = 0; i < files.length; i += BATCH) post_({ files: files.slice(i, i + BATCH), first: false });
  // 2) _백업/상담원 폴더로 옮기기
  const dest = ensureFolder_(ensureFolder_(DriveApp.getFolderById(FOLDER_ID.trim()), BACKUP_DIR), t.agent);
  const moved = [];
  let failed = 0, lastError = null, done = true;
  for (let i = 0; i < files.length; i++) {
    if (Date.now() - started > budget) { done = false; break; }
    try { DriveApp.getFileById(files[i].id).moveTo(dest); moved.push(files[i].id); }
    catch (e) { failed++; lastError = String(e); }
  }
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

function walk_(folderId, agent, since, out) {
  listFiles_(folderId, since).forEach(function (f) {
    if (!AUDIO.test(f.name)) return;
    out.push({
      id: f.id, agent: agent, name: f.name, size: Number(f.size || 0), created: f.createdTime,
      durationMs: f.videoMediaMetadata && f.videoMediaMetadata.durationMillis
        ? Number(f.videoMediaMetadata.durationMillis) : null,
    });
  });
  listChildren_(folderId, true).forEach(function (sub) { walk_(sub.id, agent, since, out); });
}

function listChildren_(folderId, foldersOnly) {
  const q = "'" + folderId + "' in parents and trashed = false and mimeType " +
    (foldersOnly ? "=" : "!=") + " 'application/vnd.google-apps.folder'";
  return driveList_(q, 'files(id,name)');
}

function listFiles_(folderId, since) {
  let q = "'" + folderId + "' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'";
  if (since) q += " and createdTime > '" + since + "'";
  return driveList_(q, 'files(id,name,size,createdTime,videoMediaMetadata(durationMillis))');
}

function driveList_(q, fields) {
  const out = [];
  let pageToken = '';
  do {
    const url = 'https://www.googleapis.com/drive/v3/files?pageSize=1000&supportsAllDrives=true' +
      '&includeItemsFromAllDrives=true&corpora=allDrives&q=' + encodeURIComponent(q) +
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
