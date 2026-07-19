const util = require('util');
const exec = util.promisify(require('child_process').exec);

async function itemScript(itemUUID) {
  const content = (val) => ({
    BTTMenuItemBackgroundColor : "50,80, 190, 166.991",
    BTTMenuItemText: val
  })
  let shellScript = `say hello world`;

  let shellScriptWrapper = {
      script: shellScript, // mandatory
      launchPath: '/bin/zsh', //optional - default is /bin/bash
      parameters: '-c', 
      environmentVariables: '' //optional e.g. VAR1=/test/;VAR2=/test2/;
  };
  
  // this will execute the Apple Script and store the result in the result variable.
  let result = await runShellScript(shellScriptWrapper);
  return JSON.stringify(content(result))
	try {
  	  const { stdout, stderr } = await exec('ls');
   	  console.log('stdout:', stdout);
  	  console.log('stderr:', stderr);

    	return ;
    } catch (e) {
      console.error(e); // should contain code (exit code) and signal (that caused the termination).
      return JSON.stringify(content(e.message));
    }
}