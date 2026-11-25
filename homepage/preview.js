const root=document.documentElement;
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
const hero=document.querySelector('.hero');
const preview=document.querySelector('#product-preview');
const modes=['report','history','peers'];
const titles={report:['财务研究总览','从财报出发，建立研究全景','经营变化，一目了然'],history:['历史趋势与数据质量','把经营变化，放回时间里','跨年变化，逐年对照'],peers:['人工指定同行比较','用同年度数据，建立对照','横向研究，多一个视角']};
let currentMode=0,paused=false,visible=true,timer;
function showMode(index){
  currentMode=index;
  const name=modes[index],text=titles[name];
  document.querySelector('.workspace').dataset.view=name;
  document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===name));
  document.querySelector('#view-label').textContent=text[0];
  document.querySelector('#demo-title').textContent=text[1];
  document.querySelector('#chart-title').textContent=text[2];
  const metrics=name==='peers'?[['研究对象','目标公司'],['自主选择','可比同行'],['报告口径','同年数据'],['比较依据','统一指标']]:name==='history'?[['历史范围','回溯年限'],['原始资料','历年披露'],['跨年对照','经营变化'],['数据核对','重述检查']]:[['规模与增长','营业收入'],['盈利与回报','盈利质量'],['现金与经营','现金流量'],['资产与效率','营运效率']];
  document.querySelectorAll('.metric-grid article').forEach((node,i)=>{node.querySelector('span').textContent=metrics[i][0];node.querySelector('strong').textContent=metrics[i][1]});
  const labels=name==='peers'?['目标公司','同行均值','同行中位数']:['历史披露','跨年观察','本期报告'];
  document.querySelectorAll('.chart-labels span').forEach((node,i)=>node.textContent=labels[i]);
  document.querySelector('.chart-line.primary').setAttribute('d',name==='peers'?'M100 65V150M300 85V150M500 95V150':'M4 126 150 100 295 112 440 58 596 26');
  const content=document.querySelector('.demo-content');
  content.classList.remove('switching');void content.offsetWidth;content.classList.add('switching');
}
function updatePlayback(){
  clearInterval(timer);
  const active=!paused&&!reducedMotion.matches&&!document.hidden&&visible;
  root.dataset.paused=String(!active);
  const toggle=document.querySelector('#motion-toggle');
  toggle.disabled=reducedMotion.matches;
  toggle.setAttribute('aria-pressed',String(paused||reducedMotion.matches));
  toggle.innerHTML=reducedMotion.matches?'已减少动态效果':paused?'继续演示 <span>▷</span>':'暂停演示 <span>Ⅱ</span>';
  if(active)timer=setInterval(()=>showMode((currentMode+1)%modes.length),7000);
}
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{showMode(modes.indexOf(button.dataset.mode));updatePlayback()}));
document.querySelector('#motion-toggle').addEventListener('click',event=>{
  paused=!paused;
  updatePlayback();
});
document.addEventListener('visibilitychange',updatePlayback);
reducedMotion.addEventListener('change',updatePlayback);
const inView=new IntersectionObserver(entries=>{for(const entry of entries){if(entry.isIntersecting){entry.target.classList.add('visible');inView.unobserve(entry.target)}}},{threshold:.1});
document.querySelectorAll('.reveal').forEach(node=>inView.observe(node));
const previewObserver=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;updatePlayback()},{threshold:.08});
previewObserver.observe(preview);
const heroObserver=new IntersectionObserver(entries=>hero.classList.toggle('offscreen',!entries[0].isIntersecting));
heroObserver.observe(hero);
let frame;
window.addEventListener('scroll',()=>{
  if(frame||reducedMotion.matches)return;
  frame=requestAnimationFrame(()=>{hero.style.setProperty('--scroll-progress',Math.min(1,window.scrollY/hero.clientHeight).toFixed(3));frame=undefined});
},{passive:true});
updatePlayback();
