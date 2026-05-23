/**
 * EdgeTTSProvider.js
 * Robust FREE TTS provider for Hermes
 */

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { Logger } = require('../core/logger');

class EdgeTTSProvider {
    constructor() {
        this.logger = new Logger('EdgeTTS');
        this.available = null;
    }

    async checkAvailability() {
        if (this.available !== null) {
            return this.available;
        }

        try {
            const ok = await this.runCommand(
                process.platform === 'win32'
                    ? 'py'
                    : 'python',
                ['-m', 'edge_tts', '--help'],
                10000
            );

            this.available = ok.success;

            if (ok.success) {
                this.logger.info('Edge-TTS detected');
            } else {
                this.logger.warn(
                    'edge-tts missing. Install with: pip install edge-tts'
                );
            }

            return this.available;

        } catch {

            this.available=false;

            return false;
        }
    }

    isAvailable() {
        return this.available===true;
    }

    runCommand(command,args,timeout=60000){

        return new Promise((resolve)=>{

            const child=spawn(
                command,
                args,
                {
                    shell:false
                }
            );

            let stdout='';
            let stderr='';

            child.stdout.on(
                'data',
                d=>stdout+=d.toString()
            );

            child.stderr.on(
                'data',
                d=>stderr+=d.toString()
            );

            const timer=setTimeout(()=>{

                child.kill();

                resolve({
                    success:false,
                    stderr:'Timeout'
                });

            },timeout);

            child.on(
                'close',
                code=>{

                    clearTimeout(timer);

                    resolve({
                        success:code===0,
                        stdout,
                        stderr
                    });

                }
            );

        });

    }

    async textToSpeech(
        text,
        outputPath,
        options={}
    ){

        const available=await this.checkAvailability();

        if(!available){

            throw new Error(
                'Edge-TTS unavailable'
            );
        }

        const voiceMap={

            curious:'en-US-JennyNeural',
            explainer:'en-US-GuyNeural',

            nova:'en-US-JennyNeural',
            onyx:'en-US-GuyNeural'

        };

        let voice=
            voiceMap[options.voice]
            || options.voice
            || 'en-US-JennyNeural';

        this.logger.info(
            `Generating TTS: ${voice}`
        );

        const python=
            process.platform==='win32'
            ? 'py'
            : 'python';

        const result=
            await this.runCommand(
                python,
                [
                    '-m',
                    'edge_tts',
                    '--voice',
                    voice,
                    '--text',
                    text,
                    '--write-media',
                    outputPath
                ],
                120000
            );

        if(
            !result.success
        ){

            throw new Error(
                result.stderr
            );
        }

        if(
            !fs.existsSync(outputPath)
        ){

            throw new Error(
                'Audio file missing'
            );
        }

        const size=
            fs.statSync(
                outputPath
            ).size;

        if(size<1000){

            throw new Error(
                'Audio file empty'
            );
        }

        this.logger.info(
            `Audio generated: ${path.basename(outputPath)}`
        );

        return outputPath;

    }

}

module.exports={
    EdgeTTSProvider
};
