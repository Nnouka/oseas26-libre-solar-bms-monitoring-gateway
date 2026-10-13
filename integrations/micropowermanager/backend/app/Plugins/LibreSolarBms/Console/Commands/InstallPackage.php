<?php

namespace App\Plugins\LibreSolarBms\Console\Commands;

use Illuminate\Console\Command;

class InstallPackage extends Command {
    protected $signature = 'libre-solar-bms:install';
    protected $description = 'Install LibreSolarBms Package';

    public function handle(): void {
        $this->info('Installing LibreSolarBms Integration Package');
        $this->info('Nothing to configure: create an API key in MPM (Settings > API keys) and give it to the mpm-ingestor service.');
        $this->info('Package installed successfully..');
    }
}
