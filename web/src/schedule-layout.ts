import { html, type Segment, type Flight } from "./workbench-model";

export function flightHeader(column: string, flights: Flight[]): string {
  const flight = flights.find(f => f.flightNo === column);
  const time = flight?.windowStart && flight.windowEnd ? `<span class="flight-time">${html(flight.windowStart)}–${html(flight.windowEnd)}</span>` : "";
  return `<div class="schedule-flight-head"><strong class="flight-number">${html(column)}</strong>${time}</div>`;
}

/** Merge adjacent columns only when the matrix says they share actual tasks. */
export function sharedBaggageGroups(columns: string[], matrix: Record<string, Segment[]>): { columns: string[]; segments: Segment[] }[] {
  const groups: { columns: string[]; segments: Segment[] }[] = [];
  for (const column of columns) {
    const segments = matrix[column] ?? [];
    const previous = groups.at(-1);
    if (previous && segments.some(s => previous.segments.some(p => p.task_id === s.task_id))) {
      previous.columns.push(column);
      const ids = new Set(previous.segments.map(s => s.task_id));
      previous.segments.push(...segments.filter(s => !ids.has(s.task_id)));
      previous.segments.sort((a,b) => a.start - b.start || a.end - b.end);
    } else groups.push({ columns: [column], segments: [...segments].sort((a,b) => a.start - b.start || a.end - b.end) });
  }
  return groups;
}

export function flightGrid(rows: string, columns: string[], flights: Flight[]): string {
  const source = document.createElement("table");
  source.innerHTML = `<tbody>${rows}</tbody>`;
  const original = [...source.rows];
  return `<colgroup>${columns.map(()=>`<col style="width:${40/columns.length}%"><col style="width:${60/columns.length}%">`).join("")}</colgroup><thead><tr>${columns.map(c=>`<th scope="col" colspan="2">${flightHeader(c, flights)}</th>`).join("")}</tr><tr>${columns.map(()=>"<th>岗位</th><th>人员</th>").join("")}</tr></thead><tbody>${original.map(row=>`<tr>${[...row.cells].slice(1).map(cell=>{
    const span = cell.colSpan;
    cell.colSpan = span * 2 - 1;
    return `<th class="flight-role">${row.cells[0]?.innerHTML ?? ""}</th>${cell.outerHTML}`;
  }).join("")}</tr>`).join("")}</tbody>`;
}

export function shareSheet(format: "html" | "png", date: string): void {
  const source = document.querySelector<HTMLTableElement>(".wb-matrix");
  if (!source) throw new Error("当前没有可导出的班表");
  const issueTexts = [...document.querySelectorAll("#schedule-issues li")].map(el=>el.textContent?.trim() ?? "").filter(t=>t && !t.includes("当前草稿可采用"));
  const warningNote = issueTexts.length ? "排班问题（原安排保留）\n" + issueTexts.join("\n") : "";
  const restNote = document.querySelector(".half-rest-summary")?.textContent?.trim() ?? "";
  const table = source.cloneNode(true) as HTMLTableElement;
  table.querySelectorAll("button").forEach(b=>b.remove());
  table.querySelectorAll("[data-drag-handle]").forEach(b=>b.remove());
  table.querySelectorAll("[contenteditable]").forEach(el=>el.removeAttribute("contenteditable"));
  const save=(blob: Blob)=>{const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`登机口班表-${date}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  if (format === "html") {
    save(new Blob([`<!doctype html><meta charset="utf-8"><title>登机口班表 ${html(date)}</title><style>body{font:14px "Microsoft YaHei",sans-serif;padding:24px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:10px;text-align:center}th{background:#f4f5f7}caption{font-size:20px;padding:16px}.schedule-flight-head{display:grid;gap:2px}.flight-number{color:#1565a7}.flight-time{color:#4c5661;font-size:12px;white-space:nowrap}.schedule-cell-conflict{background:#fde2e2}.schedule-cell-vacancy{background:#fff3cd}</style>${table.outerHTML}${warningNote ? `<section><h2>排班问题（原安排保留）</h2><ul>${issueTexts.map(t=>`<li>${html(t)}</li>`).join("")}</ul></section>` : ""}${restNote ? `<p>${html(restNote)}</p>` : ""}`],{type:"text/html;charset=utf-8"}));
    return;
  }
  const rows=[...table.rows], count=Math.max(...rows.map(row=>[...row.cells].reduce((n,c)=>n+c.colSpan,0)));
  const widths=Array.from({length:count},()=>130);
  const canvas=document.createElement("canvas"),ctx=canvas.getContext("2d")!;
  ctx.font='14px "Microsoft YaHei",sans-serif';
  rows.forEach(row=>{let i=0;for(const cell of row.cells){if(cell.colSpan===1)widths[i]=Math.max(widths[i],Math.min(280,ctx.measureText(cell.textContent?.trim()??"").width+30));i+=cell.colSpan;}});
  canvas.width=widths.reduce((a,b)=>a+b,0)+40;canvas.height=rows.length*48+100;
  ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle="#222";ctx.font='bold 22px "Microsoft YaHei",sans-serif';ctx.fillText(`登机口班表 ${date}`,20,40);
  rows.forEach((row,index)=>{let x=20,col=0;for(const cell of row.cells){const width=widths.slice(col,col+cell.colSpan).reduce((a,b)=>a+b,0),y=70+index*48;ctx.fillStyle=cell.classList.contains('schedule-cell-conflict')?'#fde2e2':cell.classList.contains('schedule-cell-vacancy')?'#fff3cd':cell.tagName==='TH'?'#f3f4f6':'#fff';ctx.fillRect(x,y,width,48);ctx.strokeStyle='#cbd0d6';ctx.strokeRect(x,y,width,48);ctx.fillStyle='#222';ctx.font='14px "Microsoft YaHei",sans-serif';
    const number=cell.querySelector('.flight-number'),time=cell.querySelector('.flight-time');
    if(number && time){
      ctx.fillStyle='#1565a7';ctx.font='bold 14px "Microsoft YaHei",sans-serif';ctx.fillText(number.textContent??'',x+10,y+19,width-20);
      ctx.fillStyle='#4c5661';ctx.font='12px "Microsoft YaHei",sans-serif';ctx.fillText(time.textContent??'',x+10,y+37,width-20);
    }else ctx.fillText(cell.textContent?.trim()??'',x+10,y+29,width-20);
    x+=width;col+=cell.colSpan;}});
  if(restNote || warningNote){
    const text=[restNote,warningNote].filter(Boolean).join("\n"), lines:string[]=[];let line="";ctx.font='14px "Microsoft YaHei",sans-serif';
    for(const char of text){if(char==="\n"){lines.push(line);line="";continue;}if(ctx.measureText(line+char).width>canvas.width-40){lines.push(line);line="";}line+=char;}if(line)lines.push(line);
    const previous=document.createElement("canvas");previous.width=canvas.width;previous.height=canvas.height;previous.getContext("2d")!.drawImage(canvas,0,0);
    const top=canvas.height;canvas.height+=lines.length*22+12;ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(previous,0,0);ctx.fillStyle="#222";ctx.font='14px "Microsoft YaHei",sans-serif';lines.forEach((value,index)=>ctx.fillText(value,20,top+index*22));
  }
  canvas.toBlob(blob=>{if(blob)save(blob);},"image/png");
}
