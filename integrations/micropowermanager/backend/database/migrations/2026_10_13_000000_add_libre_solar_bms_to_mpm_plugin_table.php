<?php

use App\Models\MpmPlugin;
use Carbon\Carbon;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

return new class extends Migration {
    public function up(): void {
        DB::table('mpm_plugins')->insert([
            [
                'id' => MpmPlugin::LIBRE_SOLAR_BMS,
                'name' => 'LibreSolarBms',
                'description' => 'Remote battery health monitoring for Libre Solar BMS: state of charge, health, imbalance, temperature and fault log.',
                'installation_command' => 'libre-solar-bms:install',
                'root_class' => 'LibreSolarBms',
                'created_at' => Carbon::now(),
                'updated_at' => Carbon::now(),
            ],
        ]);
    }

    public function down(): void {
        DB::table('mpm_plugins')->where('id', MpmPlugin::LIBRE_SOLAR_BMS)->delete();
    }
};
