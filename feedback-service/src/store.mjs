// D1 prepared statements; no project source or GitHub credential is stored here.
export class Store {
  constructor(db) { this.db = db; }
  get(id) { return this.db.prepare('SELECT * FROM reports WHERE report_id=?').bind(id).first(); }
  getNumber(number) { return this.db.prepare('SELECT * FROM reports WHERE number=?').bind(number).first(); }
  async accept(r) {
    // One conditional INSERT enforces limits even with concurrent submissions.
    await this.db.prepare(`INSERT INTO reports
      (report_id,receipt_hash,payload_hash,payload,client_hash,created_at,status)
      SELECT ?1,?2,?3,?4,?5,?6,?7
      WHERE (SELECT count(*) FROM reports WHERE client_hash=?5 AND created_at>?6-60000)<120
      AND (SELECT count(*) FROM reports WHERE client_hash=?5 AND created_at>?6-3600000)<1000
      AND (SELECT count(*) FROM reports WHERE created_at>?6-86400000)<2000
      ON CONFLICT(report_id) DO NOTHING`).bind(
      r.report_id,r.receipt_hash,r.payload_hash,r.payload,r.client_hash,r.created_at,r.status||'pending').run();
    return this.get(r.report_id);
  }
  ready(id) {return this.db.prepare("UPDATE reports SET status='pending' WHERE report_id=? AND status='uploading'").bind(id).run();}
  async claim(id, now) {
    // 'sending' is written before POST. A lost POST acknowledgement is never blindly retried.
    const r = await this.db.prepare(`UPDATE reports SET status='sending',
      attempts=attempts+1,lease_until=? WHERE report_id=? AND status='pending' AND next_attempt<=?`)
      .bind(now+120000,id,now).run();
    return r.meta.changes === 1;
  }
  done(id, number) {
    return this.db.prepare(`UPDATE reports SET status='synced',issue_number=?,last_error='',lease_until=0 WHERE report_id=?`)
      .bind(number,id).run();
  }
  retry(id, error, now, attempts) {
    return this.db.prepare(`UPDATE reports SET status='pending',last_error=?,next_attempt=?,lease_until=0 WHERE report_id=?`)
      .bind(error,now+Math.min(86400000,600000*2**Math.min(attempts,7)),id).run();
  }
  uncertain(id, error, now) {
    return this.db.prepare(`UPDATE reports SET status='uncertain',last_error=?,next_attempt=?,lease_until=0 WHERE report_id=?`)
      .bind(error,now+600000,id).run();
  }
  async due(now) {
    await this.db.prepare(`UPDATE reports SET status='uncertain',last_error='delivery_interrupted'
      WHERE status='sending' AND lease_until<?`).bind(now).run();
    const r = await this.db.prepare(`SELECT * FROM reports
      WHERE status IN ('pending','uncertain') AND next_attempt<=? ORDER BY next_attempt,number LIMIT 3`).bind(now).all();
    return r.results;
  }
}
