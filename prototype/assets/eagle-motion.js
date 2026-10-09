/* Small transparent character field; static core cached, moving accents at 8 fps. */
(()=>{
const canvas=document.getElementById('eagleCanvas'),host=document.getElementById('eagle'),grid=window.EAGLE_GRID;
if(!canvas||!host||!grid)return;
const ctx=canvas.getContext('2d');if(!ctx)return;
const W=320,H=200,dpr=Math.min(devicePixelRatio||1,2);canvas.width=W*dpr;canvas.height=H*dpr;ctx.scale(dpr,dpr);
const base=document.createElement('canvas');base.width=canvas.width;base.height=canvas.height;const b=base.getContext('2d');if(!b)return;b.scale(dpr,dpr);
const glyphs=['/','\\',':','·','—','+'];let timer=null,hovered=false,t=0;
const reduced=()=>document.body.classList.contains('reduced')||matchMedia('(prefers-reduced-motion: reduce)').matches;
const cells=grid.cells.map(([x,y],i)=>({x:12+x*4.4,y:10+y*4.7,char:x<grid.cols/2?'\\':'/',i}));
function setup(c){c.font='7px monospace';c.textBaseline='top';c.fillStyle='#647789';}
setup(b);cells.forEach(c=>{if(c.i%10!==0)b.fillText(c.char,c.x,c.y);});
function draw(animate){ctx.clearRect(0,0,W,H);ctx.drawImage(base,0,0,W,H);setup(ctx);
cells.forEach(c=>{if(c.i%10!==0)return;const phase=t*1.3+c.i*.18;const dx=animate?Math.sin(phase)*2.4:0,dy=animate?Math.cos(phase*.7)*2.5:0;ctx.globalAlpha=animate?.55+.45*Math.abs(Math.sin(phase)):1;const ch=animate&&Math.sin(phase)>.65?glyphs[(c.i+Math.floor(t*2))%glyphs.length]:c.char;ctx.fillText(ch,c.x+dx,c.y+dy);});
// Sparse drifting glyphs sit around the eagle, never over the search controls.
for(let i=0;i<30;i++){const a=i*2.4,x=15+(i*43)%290,y=i%3===0?171+(i%5)*4:25+(i*23)%135;const edgeX=i%3===0?x:278+(i%4)*9;ctx.globalAlpha=.25+.22*Math.abs(Math.sin(a+(animate?t*.6:0)));ctx.fillText(glyphs[(i+(animate?Math.floor(t*.7):0))%glyphs.length],edgeX+(animate?Math.sin(t*.8+a)*5:0),y+(animate?Math.cos(t*.5+a)*5:0));}
ctx.globalAlpha=1;
}
function sync(){clearInterval(timer);timer=null;const s=document.body.dataset.state;const visible=s!=='results'&&!document.hidden;const active=visible&&!reduced()&&!hovered&&!host.classList.contains('show')&&(s==='cover'||s==='search');draw(active);host.classList.add('glyph-ready');if(active)timer=setInterval(()=>{t+=.125;draw(true);},125);}
host.addEventListener('pointerenter',()=>{hovered=true;sync();});host.addEventListener('pointerleave',()=>{hovered=false;sync();});host.addEventListener('focus',()=>{hovered=true;sync();});host.addEventListener('blur',()=>{hovered=false;sync();});document.addEventListener('visibilitychange',sync);window.addEventListener('pagehide',()=>clearInterval(timer));window.syncEagleMotion=sync;sync();
})();
