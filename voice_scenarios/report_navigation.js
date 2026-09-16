function initReportNavigation() {
  const button=document.getElementById('sidebar-toggle');
  const sidebar=document.getElementById('report-sidebar');
  if(!button||!sidebar)return;
  let collapsed=false;
  try{collapsed=localStorage.getItem('vas-report-sidebar')==='collapsed';}catch{}
  function draw(){
    document.body.classList.toggle('sidebar-collapsed',collapsed);
    sidebar.hidden=collapsed;
    button.setAttribute('aria-expanded',String(!collapsed));
    button.setAttribute('aria-label',collapsed?'展开会话导航':'收起会话导航');
    button.textContent=collapsed?'☰ 展开目录':'‹ 收起目录';
  }
  button.addEventListener('click',()=>{
    collapsed=!collapsed;draw();
    try{localStorage.setItem('vas-report-sidebar',collapsed?'collapsed':'expanded');}catch{}
  });
  draw();
}
