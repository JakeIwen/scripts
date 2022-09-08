dslEngineSel = '.toc-item.leaf'

printBtnPage = () => $('.fa.fa-print:visible').first();
printBtnMain = () => $('#print_details_submit:visible');

wait = async (ms) => new Promise((res) => setTimeout(() => res(true), ms));

docLinks = (selector) => $(`${selector}:visible`).toArray().reduce((acc,cur) => {
  if(!$(cur).text().includes('3.6L')) acc.push(cur);
  return acc;
}, []);

openItem = async (itm, i) => {
  text = $(itm).children('a').text();
  console.log(text)
  $(itm).children('a').children(0).click();
  await wait(750);
  document.title = text
  printBtnPage().click();
  await wait(300);
  printBtnMain().click();
  lastwait = 200;
  if(i) lastwait += 4000; // run code in child windopw to map URLs;
  await wait(200);
  console.log('done waiting')
  return true;
}

opnPdfs = async (slctrs) => {
  let items = docLinks(slctrs);
  i = 0;
  for (let i = 0; i < items.length; i++) {
    const lnk = docLinks(slctrs)[i];
    console.log('lnk', lnk)
    await openItem(lnk, i)
    
  }
}

opnPdfs(dslEngineSel)