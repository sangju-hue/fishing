/* Native booking-page routes. No cross-origin scripts or login automation. */
(function(root){
  function forDate(raw,date){
    if(!raw)return '';
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date))return raw;
    let url;
    try{url=new URL(raw)}catch(e){return raw}
    if(!['http:','https:'].includes(url.protocol))return '';
    const [year,month,day]=date.split('-'),q=url.searchParams;
    if(url.hostname.endsWith('.sunsang24.com')){
      if(/\/mypage\/reservation_ready\/\d+/.test(url.pathname))return url.href;
      if(url.hostname==='www.sunsang24.com'||url.pathname.startsWith('/ship/detail/'))return url.href;
      url.protocol='https:';url.pathname='/ship/schedule_fleet/'+year+month;url.search='';url.hash='d'+date;
      return url.href;
    }
    if(url.hostname.replace(/^www\./,'')==='kukjaenaksi.com'){
      url.pathname='/niabbs5m/inc.php';url.search='';url.searchParams.set('inc','sub2');url.searchParams.set('toYear',year);url.searchParams.set('toMonth',month);url.hash='';return url.href;
    }
    if(/\/niabbs5m?\//.test(url.pathname)){
      // inc.php ignores date parameters on these providers. The public day
      // fragment is the exact endpoint requested by their date-click handler.
      url.pathname=url.pathname.replace(/\/(?:inc\.php|doc\/sub2_in2?\.htm)$/, '/doc/sub2_in.htm');
      url.search='';url.searchParams.set('toYear',year);url.searchParams.set('toMonth',month);url.searchParams.set('callday',day);url.hash='';
      return url.href;
    }
    if(url.hostname.replace(/^www\./,'')==='hanaho.net'){
      url.pathname='/ship/booking.php';url.search='';q.set('ymd',year+month+day);url.hash='';return url.href;
    }
    if(q.get('hid')==='status'){
      q.set('sch_year',year);q.set('sch_month',month);q.set('sch_day',day);url.hash='';return url.href;
    }
    if(url.hostname.replace(/^www\./,'')==='blueseaho.com'){
      url.pathname='/reservation';url.search='';url.searchParams.set('search_date',year+'-'+month);url.searchParams.set('day',day);url.hash='';return url.href;
    }
    if(q.get('mid')==='bk'||q.has('sel')||q.has('PA_N_UID')){
      q.set('mid','bk');q.set('year',year);q.set('month',month);q.set('day',day);q.set('mode','list');q.set('sel','day');q.set('won','1');url.hash='list';
    }
    return url.href;
  }
  const api={forDate};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.BookingRoutes=api;
})(typeof window!=='undefined'?window:this);
