import json
import os
import subprocess
import logging
import threading
import time
from datetime import datetime
from app.extensions import db
from app.models.tool import Tool
from app.config import Config

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = [
    # === Essential Tools ===
    {"name": "nmap", "display_name": "Nmap Network Mapper", "category": "essential", "description": "Network discovery and security auditing.", "dependencies": {"apt": ["nmap"]}, "health_check_cmd": "nmap --version"},
    {"name": "gobuster", "display_name": "Gobuster", "category": "essential", "description": "Directory/file & DNS busting tool.", "dependencies": {"apt": ["gobuster"]}, "health_check_cmd": "gobuster version"},
    {"name": "dirb", "display_name": "DIRB", "category": "essential", "description": "Web Content Scanner - URL brute forcing.", "dependencies": {"apt": ["dirb"]}, "health_check_cmd": "dirb --help"},
    {"name": "nikto", "display_name": "Nikto", "category": "essential", "description": "Web server vulnerability scanner.", "dependencies": {"apt": ["nikto"]}, "health_check_cmd": "nikto -Version"},
    {"name": "sqlmap", "display_name": "SQLMap", "category": "essential", "description": "Automatic SQL injection and database takeover tool.", "dependencies": {"apt": ["sqlmap"]}, "health_check_cmd": "sqlmap --version"},
    {"name": "hydra", "display_name": "Hydra", "category": "essential", "description": "Network logon cracker.", "dependencies": {"apt": ["hydra"]}, "health_check_cmd": "hydra -h"},
    {"name": "john", "display_name": "John the Ripper", "category": "essential", "description": "Password cracker.", "dependencies": {"apt": ["john"]}, "health_check_cmd": "john --help"},
    {"name": "hashcat", "display_name": "Hashcat", "category": "essential", "description": "Advanced password recovery tool.", "dependencies": {"apt": ["hashcat"]}, "health_check_cmd": "hashcat --version"},

    # === Network Tools ===
    {"name": "rustscan", "display_name": "RustScan", "category": "network", "description": "Modern port scanner focused on speed.", "dependencies": {"apt": ["rustscan"]}, "health_check_cmd": "rustscan --version"},
    {"name": "masscan", "display_name": "Masscan", "category": "network", "description": "Internet-scale port scanner.", "dependencies": {"apt": ["masscan"]}, "health_check_cmd": "masscan --version"},
    {"name": "autorecon", "display_name": "AutoRecon", "category": "network", "description": "Multi-threaded network reconnaissance tool.", "dependencies": {"apt": ["autorecon"]}, "health_check_cmd": "autorecon --help"},
    {"name": "nbtscan", "display_name": "NBTScan", "category": "network", "description": "NetBIOS name service scanner.", "dependencies": {"apt": ["nbtscan"]}, "health_check_cmd": "nbtscan --version"},
    {"name": "arp-scan", "display_name": "ARP-Scan", "category": "network", "description": "ARP scanning and fingerprinting.", "dependencies": {"apt": ["arp-scan"]}, "health_check_cmd": "arp-scan --version"},
    {"name": "responder", "display_name": "Responder", "category": "network", "description": "LLMNR/NBT-NS/mDNS poisoner.", "dependencies": {"apt": ["responder"]}, "health_check_cmd": "responder -h"},
    {"name": "nxc", "display_name": "NetExec", "category": "network", "description": "Network execution tool (formerly CrackMapExec).", "dependencies": {"apt": ["netexec"]}, "health_check_cmd": "nxc --version"},
    {"name": "enum4linux-ng", "display_name": "Enum4linux-ng", "category": "network", "description": "Windows/Samba enumeration tool.", "dependencies": {"apt": ["enum4linux-ng"]}, "health_check_cmd": "enum4linux-ng -h"},
    {"name": "rpcclient", "display_name": "RPCClient", "category": "network", "description": "RPC connection tool for Samba.", "dependencies": {"apt": ["smbclient"]}, "health_check_cmd": "rpcclient --version"},
    {"name": "enum4linux", "display_name": "Enum4linux", "category": "network", "description": "Windows/Samba enumeration tool (legacy).", "dependencies": {"apt": ["enum4linux"]}, "health_check_cmd": "enum4linux -h"},

    # === Web Security Tools ===
    {"name": "ffuf", "display_name": "FFuF", "category": "web_security", "description": "Fast web fuzzer.", "dependencies": {"apt": ["ffuf"]}, "health_check_cmd": "ffuf -V"},
    {"name": "feroxbuster", "display_name": "Feroxbuster", "category": "web_security", "description": "Fast, recursive content discovery tool.", "dependencies": {"apt": ["feroxbuster"]}, "health_check_cmd": "feroxbuster --version"},
    {"name": "dirsearch", "display_name": "Dirsearch", "category": "web_security", "description": "Web path scanner.", "dependencies": {"apt": ["dirsearch"]}, "health_check_cmd": "dirsearch -h"},
    {"name": "dotdotpwn", "display_name": "DotDotPwn", "category": "web_security", "description": "Directory traversal fuzzer.", "dependencies": {"apt": ["dotdotpwn"]}, "health_check_cmd": "dotdotpwn -h"},
    {"name": "xsser", "display_name": "XSSer", "category": "web_security", "description": "Cross-site scripting scanner.", "dependencies": {"apt": ["xsser"]}, "health_check_cmd": "xsser --help"},
    {"name": "wfuzz", "display_name": "WFuzz", "category": "web_security", "description": "Web application fuzzer.", "dependencies": {"apt": ["wfuzz"]}, "health_check_cmd": "wfuzz --version"},
    {"name": "gau", "display_name": "GAU", "category": "web_security", "description": "Get All URLs - fetches URLs from AlienVault, Wayback, Common Crawl.", "dependencies": {"apt": ["gau"]}, "health_check_cmd": "gau --version"},
    {"name": "waybackurls", "display_name": "Waybackurls", "category": "web_security", "description": "Fetch URLs from Wayback Machine.", "dependencies": {"apt": ["waybackurls"]}, "health_check_cmd": "waybackurls --help"},
    {"name": "arjun", "display_name": "Arjun", "category": "web_security", "description": "HTTP parameter discovery tool.", "dependencies": {"apt": ["arjun"]}, "health_check_cmd": "arjun --help"},
    {"name": "paramspider", "display_name": "ParamSpider", "category": "web_security", "description": "Parameter discovery tool.", "dependencies": {"apt": ["paramspider"]}, "health_check_cmd": "paramspider --help"},
    {"name": "x8", "display_name": "X8", "category": "web_security", "description": "Hidden URL discovery tool.", "dependencies": {"apt": ["x8"]}, "health_check_cmd": "x8 --version"},
    {"name": "jaeles", "display_name": "Jaeles", "category": "web_security", "description": "Advanced vulnerability scanner.", "dependencies": {"apt": ["jaeles"]}, "health_check_cmd": "jaeles config -h"},
    {"name": "dalfox", "display_name": "Dalfox", "category": "web_security", "description": "XSS scanning tool.", "dependencies": {"apt": ["dalfox"]}, "health_check_cmd": "dalfox version"},
    {"name": "httpx", "display_name": "HTTPX", "category": "web_security", "description": "Fast HTTP toolkit.", "dependencies": {"apt": ["httpx"]}, "health_check_cmd": "httpx -version"},
    {"name": "wafw00f", "display_name": "WAFW00F", "category": "web_security", "description": "Web Application Firewall detection.", "dependencies": {"apt": ["wafw00f"]}, "health_check_cmd": "wafw00f --version"},
    {"name": "burpsuite", "display_name": "Burp Suite", "category": "web_security", "description": "Web vulnerability scanner & proxy.", "dependencies": {"apt": ["burpsuite"]}, "health_check_cmd": "burpsuite --version"},
    {"name": "zaproxy", "display_name": "OWASP ZAP", "category": "web_security", "description": "Web application security scanner.", "dependencies": {"apt": ["zaproxy"]}, "health_check_cmd": "zaproxy --version"},
    {"name": "katana", "display_name": "Katana", "category": "web_security", "description": "Next-generation crawling framework.", "dependencies": {"apt": ["katana"]}, "health_check_cmd": "katana -version"},
    {"name": "hakrawler", "display_name": "Hakrawler", "category": "web_security", "description": "Simple web crawler for quick discovery.", "dependencies": {"apt": ["hakrawler"]}, "health_check_cmd": "hakrawler --help"},

    # === Vulnerability Scanning ===
    {"name": "nuclei", "display_name": "Nuclei", "category": "vuln_scanning", "description": "Fast vulnerability scanner.", "dependencies": {"apt": ["nuclei"]}, "health_check_cmd": "nuclei -version"},
    {"name": "wpscan", "display_name": "WPScan", "category": "vuln_scanning", "description": "WordPress security scanner.", "dependencies": {"apt": ["wpscan"]}, "health_check_cmd": "wpscan --version"},

    # === Password Tools ===
    {"name": "medusa", "display_name": "Medusa", "category": "password", "description": "Parallel network login cracker.", "dependencies": {"apt": ["medusa"]}, "health_check_cmd": "medusa -h"},
    {"name": "patator", "display_name": "Patator", "category": "password", "description": "Multi-purpose brute forcer.", "dependencies": {"apt": ["patator"]}, "health_check_cmd": "patator -h"},
    {"name": "hash-identifier", "display_name": "Hash Identifier", "category": "password", "description": "Hash type identification tool.", "dependencies": {"apt": ["hash-identifier"]}, "health_check_cmd": "hash-identifier"},
    {"name": "ophcrack", "display_name": "Ophcrack", "category": "password", "description": "Windows password cracker.", "dependencies": {"apt": ["ophcrack"]}, "health_check_cmd": "ophcrack --help"},
    {"name": "hashcat-utils", "display_name": "Hashcat Utils", "category": "password", "description": "Utilities for Hashcat.", "dependencies": {"apt": ["hashcat-utils"]}, "health_check_cmd": "hashcat-utils --help"},

    # === Binary Analysis Tools ===
    {"name": "gdb", "display_name": "GDB", "category": "binary", "description": "GNU Debugger.", "dependencies": {"apt": ["gdb"]}, "health_check_cmd": "gdb --version"},
    {"name": "radare2", "display_name": "Radare2", "category": "binary", "description": "Reverse engineering framework.", "dependencies": {"apt": ["radare2"]}, "health_check_cmd": "radare2 -v"},
    {"name": "binwalk", "display_name": "Binwalk", "category": "binary", "description": "Firmware analysis tool.", "dependencies": {"apt": ["binwalk"]}, "health_check_cmd": "binwalk --version"},
    {"name": "ropgadget", "display_name": "ROPgadget", "category": "binary", "description": "ROP chain generator.", "dependencies": {"apt": ["ropgadget"]}, "health_check_cmd": "ROPgadget --version"},
    {"name": "checksec", "display_name": "Checksec", "category": "binary", "description": "Binary security properties checker.", "dependencies": {"apt": ["checksec"]}, "health_check_cmd": "checksec --version"},
    {"name": "objdump", "display_name": "Objdump", "category": "binary", "description": "Object file disassembler.", "dependencies": {"apt": ["binutils"]}, "health_check_cmd": "objdump --version"},
    {"name": "ghidra", "display_name": "Ghidra", "category": "binary", "description": "Reverse engineering suite by NSA.", "dependencies": {"apt": ["ghidra"]}, "health_check_cmd": "ghidra --version"},
    {"name": "one-gadget", "display_name": "One-Gadget", "category": "binary", "description": "glibc RCE gadget finder.", "dependencies": {"apt": ["one-gadget"]}, "health_check_cmd": "one-gadget --version"},
    {"name": "ropper", "display_name": "Ropper", "category": "binary", "description": "ROP gadget finder.", "dependencies": {"apt": ["ropper"]}, "health_check_cmd": "ropper --version"},
    {"name": "angr", "display_name": "Angr", "category": "binary", "description": "Binary analysis framework.", "dependencies": {"apt": ["angr"]}, "health_check_cmd": "python3 -c 'import angr; print(angr.__version__)'"},
    {"name": "libc-database", "display_name": "libc-database", "category": "binary", "description": "libc database for CTF.", "dependencies": {"apt": ["libc-database"]}, "health_check_cmd": "libc-database --help"},
    {"name": "pwninit", "display_name": "Pwninit", "category": "binary", "description": "Automate starting binary exploitation.", "dependencies": {"apt": ["pwninit"]}, "health_check_cmd": "pwninit --help"},

    # === Forensics Tools ===
    {"name": "volatility3", "display_name": "Volatility 3", "category": "forensics", "description": "Memory forensics framework.", "dependencies": {"apt": ["volatility3"]}, "health_check_cmd": "volatility3 --help"},
    {"name": "vol", "display_name": "Vol (Volatility 2)", "category": "forensics", "description": "Volatility 2 memory forensics.", "dependencies": {"apt": ["volatility"]}, "health_check_cmd": "vol.py --help"},
    {"name": "steghide", "display_name": "Steghide", "category": "forensics", "description": "Steganography tool.", "dependencies": {"apt": ["steghide"]}, "health_check_cmd": "steghide --version"},
    {"name": "hashpump", "display_name": "HashPump", "category": "forensics", "description": "Hash length extension tool.", "dependencies": {"apt": ["hashpump"]}, "health_check_cmd": "hashpump --help"},
    {"name": "foremost", "display_name": "Foremost", "category": "forensics", "description": "File carving tool.", "dependencies": {"apt": ["foremost"]}, "health_check_cmd": "foremost -h"},
    {"name": "exiftool", "display_name": "ExifTool", "category": "forensics", "description": "Metadata reader/writer.", "dependencies": {"apt": ["exiftool"]}, "health_check_cmd": "exiftool -ver"},
    {"name": "strings", "display_name": "Strings", "category": "forensics", "description": "Print strings from binaries.", "dependencies": {"apt": ["binutils"]}, "health_check_cmd": "strings --version"},
    {"name": "xxd", "display_name": "XXD", "category": "forensics", "description": "Hex dump tool.", "dependencies": {"apt": ["xxd"]}, "health_check_cmd": "xxd --version"},
    {"name": "file", "display_name": "File", "category": "forensics", "description": "File type identification.", "dependencies": {"apt": ["file"]}, "health_check_cmd": "file --version"},
    {"name": "photorec", "display_name": "PhotoRec", "category": "forensics", "description": "File recovery tool.", "dependencies": {"apt": ["photorec"]}, "health_check_cmd": "photorec --help"},
    {"name": "testdisk", "display_name": "TestDisk", "category": "forensics", "description": "Partition recovery tool.", "dependencies": {"apt": ["testdisk"]}, "health_check_cmd": "testdisk --help"},
    {"name": "scalpel", "display_name": "Scalpel", "category": "forensics", "description": "File carving tool.", "dependencies": {"apt": ["scalpel"]}, "health_check_cmd": "scalpel -h"},
    {"name": "bulk-extractor", "display_name": "Bulk Extractor", "category": "forensics", "description": "Forensic data extractor.", "dependencies": {"apt": ["bulk-extractor"]}, "health_check_cmd": "bulk_extractor -h"},
    {"name": "stegsolve", "display_name": "StegSolve", "category": "forensics", "description": "Steganography image analyzer.", "dependencies": {"apt": ["stegsolve"]}, "health_check_cmd": "stegsolve -h"},
    {"name": "zsteg", "display_name": "ZSteg", "category": "forensics", "description": "PNG/BMP steganography detector.", "dependencies": {"apt": ["zsteg"]}, "health_check_cmd": "zsteg --help"},
    {"name": "outguess", "display_name": "OutGuess", "category": "forensics", "description": "Universal steganographic tool.", "dependencies": {"apt": ["outguess"]}, "health_check_cmd": "outguess -h"},

    # === Cloud Security Tools ===
    {"name": "prowler", "display_name": "Prowler", "category": "cloud", "description": "AWS security assessment.", "dependencies": {"apt": ["prowler"]}, "health_check_cmd": "prowler --version"},
    {"name": "scout-suite", "display_name": "Scout Suite", "category": "cloud", "description": "Multi-cloud security audit.", "dependencies": {"apt": ["scoutsuite"]}, "health_check_cmd": "scout --version"},
    {"name": "trivy", "display_name": "Trivy", "category": "cloud", "description": "Container & cloud vulnerability scanner.", "dependencies": {"apt": ["trivy"]}, "health_check_cmd": "trivy --version"},
    {"name": "kube-hunter", "display_name": "Kube-Hunter", "category": "cloud", "description": "Kubernetes security scanner.", "dependencies": {"apt": ["kube-hunter"]}, "health_check_cmd": "kube-hunter --list"},
    {"name": "kube-bench", "display_name": "Kube-Bench", "category": "cloud", "description": "Kubernetes CIS benchmark.", "dependencies": {"apt": ["kube-bench"]}, "health_check_cmd": "kube-bench --version"},
    {"name": "docker-bench-security", "display_name": "Docker Bench", "category": "cloud", "description": "Docker security checker.", "dependencies": {"apt": ["docker-bench-security"]}, "health_check_cmd": "docker-bench-security --version"},
    {"name": "checkov", "display_name": "Checkov", "category": "cloud", "description": "Infrastructure as Code scanner.", "dependencies": {"apt": ["checkov"]}, "health_check_cmd": "checkov --version"},
    {"name": "terrascan", "display_name": "Terrascan", "category": "cloud", "description": "IaC security scanner.", "dependencies": {"apt": ["terrascan"]}, "health_check_cmd": "terrascan version"},
    {"name": "falco", "display_name": "Falco", "category": "cloud", "description": "Runtime threat detection.", "dependencies": {"apt": ["falco"]}, "health_check_cmd": "falco --version"},
    {"name": "clair", "display_name": "Clair", "category": "cloud", "description": "Container image scanner.", "dependencies": {"apt": ["clair"]}, "health_check_cmd": "clair --version"},

    # === OSINT Tools ===
    {"name": "amass", "display_name": "OWASP Amass", "category": "osint", "description": "Attack surface mapping.", "dependencies": {"apt": ["amass"]}, "health_check_cmd": "amass -version"},
    {"name": "subfinder", "display_name": "Subfinder", "category": "osint", "description": "Subdomain discovery.", "dependencies": {"apt": ["subfinder"]}, "health_check_cmd": "subfinder -version"},
    {"name": "fierce", "display_name": "Fierce", "category": "osint", "description": "DNS reconnaissance.", "dependencies": {"apt": ["fierce"]}, "health_check_cmd": "fierce --help"},
    {"name": "dnsenum", "display_name": "DNSenum", "category": "osint", "description": "DNS enumeration script.", "dependencies": {"apt": ["dnsenum"]}, "health_check_cmd": "dnsenum --help"},
    {"name": "theharvester", "display_name": "theHarvester", "category": "osint", "description": "Email/subdomain/IP harvester.", "dependencies": {"apt": ["theharvester"]}, "health_check_cmd": "theharvester -h"},
    {"name": "sherlock", "display_name": "Sherlock", "category": "osint", "description": "Social media username search.", "dependencies": {"apt": ["sherlock"]}, "health_check_cmd": "sherlock --help"},
    {"name": "social-analyzer", "display_name": "Social Analyzer", "category": "osint", "description": "Social media analysis.", "dependencies": {"apt": ["social-analyzer"]}, "health_check_cmd": "social-analyzer --help"},
    {"name": "recon-ng", "display_name": "Recon-ng", "category": "osint", "description": "Web-based reconnaissance.", "dependencies": {"apt": ["recon-ng"]}, "health_check_cmd": "recon-ng --version"},
    {"name": "maltego", "display_name": "Maltego", "category": "osint", "description": "Data mining & link analysis.", "dependencies": {"apt": ["maltego"]}, "health_check_cmd": "maltego --version"},
    {"name": "spiderfoot", "display_name": "SpiderFoot", "category": "osint", "description": "Automated OSINT.", "dependencies": {"apt": ["spiderfoot"]}, "health_check_cmd": "spiderfoot --help"},
    {"name": "shodan", "display_name": "Shodan", "category": "osint", "description": "Internet-wide scanner CLI.", "dependencies": {"apt": ["shodan"]}, "health_check_cmd": "shodan --version"},
    {"name": "censys-cli", "display_name": "Censys CLI", "category": "osint", "description": "Internet search engine CLI.", "dependencies": {"apt": ["censys"]}, "health_check_cmd": "censys --version"},
    {"name": "have-i-been-pwned", "display_name": "Have I Been Pwned", "category": "osint", "description": "Breach checking tool.", "dependencies": {"apt": ["hibp"]}, "health_check_cmd": "hibp --help"},

    # === Exploitation Tools ===
    {"name": "msfconsole", "display_name": "Metasploit", "category": "exploitation", "description": "Exploitation framework.", "dependencies": {"apt": ["metasploit-framework"]}, "health_check_cmd": "msfconsole --version"},
    {"name": "exploit-db", "display_name": "Exploit DB", "category": "exploitation", "description": "Exploit database.", "dependencies": {"apt": ["exploitdb"]}, "health_check_cmd": "searchsploit --version"},
    {"name": "searchsploit", "display_name": "Searchsploit", "category": "exploitation", "description": "Exploit DB search tool.", "dependencies": {"apt": ["exploitdb"]}, "health_check_cmd": "searchsploit --help"},

    # === API Tools ===
    {"name": "api-schema-analyzer", "display_name": "API Schema Analyzer", "category": "api", "description": "API schema security analyzer.", "dependencies": {"apt": ["api-schema-analyzer"]}, "health_check_cmd": "api-schema-analyzer --help"},
    {"name": "curl", "display_name": "cURL", "category": "api", "description": "HTTP client.", "dependencies": {"apt": ["curl"]}, "health_check_cmd": "curl --version"},
    {"name": "httpie", "display_name": "HTTPie", "category": "api", "description": "Modern HTTP client.", "dependencies": {"apt": ["httpie"]}, "health_check_cmd": "http --version"},
    {"name": "anew", "display_name": "Anew", "category": "api", "description": "Append-only wordlist tool.", "dependencies": {"apt": ["anew"]}, "health_check_cmd": "anew --help"},
    {"name": "qsreplace", "display_name": "QSReplace", "category": "api", "description": "Query string replacement tool.", "dependencies": {"apt": ["qsreplace"]}, "health_check_cmd": "qsreplace --help"},
    {"name": "uro", "display_name": "Uro", "category": "api", "description": "URL optimization tool.", "dependencies": {"apt": ["uro"]}, "health_check_cmd": "uro --help"},

    # === Wireless Tools ===
    {"name": "kismet", "display_name": "Kismet", "category": "wireless", "description": "Wireless network detector.", "dependencies": {"apt": ["kismet"]}, "health_check_cmd": "kismet --version"},
    {"name": "wireshark", "display_name": "Wireshark", "category": "wireless", "description": "Network protocol analyzer.", "dependencies": {"apt": ["wireshark"]}, "health_check_cmd": "wireshark --version"},
    {"name": "tshark", "display_name": "TShark", "category": "wireless", "description": "CLI network analyzer.", "dependencies": {"apt": ["tshark"]}, "health_check_cmd": "tshark --version"},
    {"name": "tcpdump", "display_name": "Tcpdump", "category": "wireless", "description": "Packet analyzer.", "dependencies": {"apt": ["tcpdump"]}, "health_check_cmd": "tcpdump --version"},

    # === Additional Tools ===
    {"name": "smbmap", "display_name": "SMBMap", "category": "additional", "description": "SMB share enumerator.", "dependencies": {"apt": ["smbmap"]}, "health_check_cmd": "smbmap --help"},
    {"name": "volatility", "display_name": "Volatility 2", "category": "additional", "description": "Memory forensics (v2).", "dependencies": {"apt": ["volatility"]}, "health_check_cmd": "vol.py --help"},
    {"name": "sleuthkit", "display_name": "Sleuth Kit", "category": "additional", "description": "Forensic toolkit.", "dependencies": {"apt": ["sleuthkit"]}, "health_check_cmd": "tsk_version"},
    {"name": "autopsy", "display_name": "Autopsy", "category": "additional", "description": "Digital forensics platform.", "dependencies": {"apt": ["autopsy"]}, "health_check_cmd": "autopsy --version"},
    {"name": "evil-winrm", "display_name": "Evil-WinRM", "category": "additional", "description": "WinRM shell.", "dependencies": {"apt": ["evil-winrm"]}, "health_check_cmd": "evil-winrm --help"},
    {"name": "airmon-ng", "display_name": "Airmon-ng", "category": "additional", "description": "Wireless monitor mode config.", "dependencies": {"apt": ["aircrack-ng"]}, "health_check_cmd": "airmon-ng --help"},
    {"name": "airodump-ng", "display_name": "Airodump-ng", "category": "additional", "description": "Wireless packet capture.", "dependencies": {"apt": ["aircrack-ng"]}, "health_check_cmd": "airodump-ng --help"},
    {"name": "aireplay-ng", "display_name": "Aireplay-ng", "category": "additional", "description": "Wireless packet injection.", "dependencies": {"apt": ["aircrack-ng"]}, "health_check_cmd": "aireplay-ng --help"},
    {"name": "aircrack-ng", "display_name": "Aircrack-ng", "category": "additional", "description": "WiFi security audit.", "dependencies": {"apt": ["aircrack-ng"]}, "health_check_cmd": "aircrack-ng --help"},
    {"name": "msfvenom", "display_name": "MSFVenom", "category": "additional", "description": "Payload generator.", "dependencies": {"apt": ["metasploit-framework"]}, "health_check_cmd": "msfvenom --help"},
    {"name": "pwntools", "display_name": "Pwntools", "category": "additional", "description": "CTF framework.", "dependencies": {"apt": ["pwntools"]}, "health_check_cmd": "python3 -c 'import pwn; print(pwn.__version__)'"},
    {"name": "jwt-analyzer", "display_name": "JWT Analyzer", "category": "additional", "description": "JWT token analyzer.", "dependencies": {"apt": ["jwt"]}, "health_check_cmd": "python3 -c 'import jwt; print(jwt.__version__)'"},
    {"name": "graphql-scanner", "display_name": "GraphQL Scanner", "category": "additional", "description": "GraphQL security scanner.", "dependencies": {"apt": ["graphql-scanner"]}, "health_check_cmd": "graphql-scanner --help"},
]

class ToolRegistry:
    # 自动健康检测相关类变量
    _auto_check_thread = None
    _auto_check_running = False
    _last_check_time = None
    _check_stats = {"total": 0, "available": 0, "unavailable": 0, "errors": 0}

    @staticmethod
    def init_tools():
        """初始化默认工具到数据库"""
        count = 0
        for t_data in DEFAULT_TOOLS:
            if not db.session.get(Tool, t_data['name']):
                tool = Tool(**t_data)
                db.session.add(tool)
                count += 1
        if count > 0:
            db.session.commit()
            logger.info(f"✅ Default tools initialized ({count} new tools)")
            
            # 初始化后立即执行一次健康检测
            logger.info("🏥 Running initial health check for all tools...")
            try:
                ToolRegistry.check_all_health()
                logger.info("✅ Initial health check completed")
            except Exception as e:
                logger.error(f"❌ Initial health check failed: {e}")
        else:
            logger.info("ℹ️ All tools already exist in database")

    @staticmethod
    def check_health(tool_name: str) -> dict:
        """执行工具健康检查"""
        tool = db.session.get(Tool, tool_name)
        if not tool:
            return {"available": False, "error": "Tool not registered"}

        try:
            for pkg_type, pkgs in (tool.dependencies or {}).items():
                if pkg_type == 'apt':
                    for pkg in pkgs:
                        if subprocess.run(['which', pkg], capture_output=True).returncode != 0:
                            raise Exception(f"Missing dependency: {pkg}")
                elif pkg_type == 'pip':
                    for pkg in pkgs:
                        if subprocess.run(['pip', 'show', pkg], capture_output=True).returncode != 0:
                            raise Exception(f"Missing pip package: {pkg}")

            result = subprocess.run(tool.health_check_cmd.split(), capture_output=True, text=True, timeout=Config.HEALTH_CHECK_TIMEOUT)
            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0] if result.stdout else "Unknown"
                tool.is_available = True
                tool.installed_version = version_line[:50]
            else:
                tool.is_available = False
                tool.installed_version = None

        except Exception as e:
            tool.is_available = False
            tool.installed_version = None
            logger.warning(f"Health check failed for {tool_name}: {e}")

        tool.last_health_check = db.func.now()
        db.session.commit()

        return {
            "name": tool.name,
            "available": tool.is_available,
            "version": tool.installed_version,
            "last_check": tool.last_health_check.isoformat() if tool.last_health_check else None
        }

    @staticmethod
    def check_all_health() -> dict:
        """批量检查所有工具健康状态"""
        tools = Tool.query.all()
        results = []
        stats = {"total": len(tools), "available": 0, "unavailable": 0, "errors": 0}

        for tool in tools:
            try:
                result = ToolRegistry.check_health(tool.name)
                results.append(result)
                if result.get("available"):
                    stats["available"] += 1
                else:
                    stats["unavailable"] += 1
            except Exception as e:
                logger.error(f"Error checking {tool.name}: {e}")
                results.append({"name": tool.name, "available": False, "error": str(e)})
                stats["errors"] += 1

        ToolRegistry._check_stats = stats
        ToolRegistry._last_check_time = datetime.now()

        return {
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
            "results": results
        }

    @staticmethod
    def _auto_health_check_loop():
        """后台自动健康检测循环"""
        logger.info(f"🔄 Auto health check started (interval: {Config.HEALTH_CHECK_INTERVAL}s)")
        
        # 启动时立即执行一次检测
        try:
            logger.info("🏥 Running initial health check on scheduler start...")
            result = ToolRegistry.check_all_health()
            stats = result["stats"]
            logger.info(
                f"✅ Initial health check completed | "
                f"Total: {stats['total']} | "
                f"Available: {stats['available']} | "
                f"Unavailable: {stats['unavailable']} | "
                f"Errors: {stats['errors']}"
            )
        except Exception as e:
            logger.error(f"❌ Initial health check failed: {e}")
        
        # 进入定时循环
        while ToolRegistry._auto_check_running:
            try:
                # 等待下一个周期
                for _ in range(Config.HEALTH_CHECK_INTERVAL):
                    if not ToolRegistry._auto_check_running:
                        break
                    time.sleep(1)
                
                if not ToolRegistry._auto_check_running:
                    break
                    
                # 执行批量检测
                logger.info("🏥 Starting periodic health check for all tools...")
                result = ToolRegistry.check_all_health()
                
                stats = result["stats"]
                logger.info(
                    f"✅ Health check completed | "
                    f"Total: {stats['total']} | "
                    f"Available: {stats['available']} | "
                    f"Unavailable: {stats['unavailable']} | "
                    f"Errors: {stats['errors']}"
                )
                    
            except Exception as e:
                logger.error(f"❌ Error in auto health check loop: {e}")
                time.sleep(60)  # 出错后等待1分钟再重试

        logger.info("🔄 Auto health check stopped")

    @staticmethod
    def start_auto_health_check():
        """启动自动健康检测"""
        if ToolRegistry._auto_check_running:
            logger.warning("⚠️ Auto health check is already running")
            return False

        if not Config.AUTO_HEALTH_CHECK:
            logger.info("ℹ️ Auto health check is disabled in config")
            return False

        ToolRegistry._auto_check_running = True
        ToolRegistry._auto_check_thread = threading.Thread(
            target=ToolRegistry._auto_health_check_loop,
            daemon=True,
            name="HealthCheckScheduler"
        )
        ToolRegistry._auto_check_thread.start()
        logger.info("✅ Auto health check scheduler started")
        return True

    @staticmethod
    def stop_auto_health_check():
        """停止自动健康检测"""
        if not ToolRegistry._auto_check_running:
            logger.warning("⚠️ Auto health check is not running")
            return False

        ToolRegistry._auto_check_running = False
        if ToolRegistry._auto_check_thread:
            ToolRegistry._auto_check_thread.join(timeout=10)
        
        logger.info("✅ Auto health check scheduler stopped")
        return True

    @staticmethod
    def get_auto_check_status() -> dict:
        """获取自动健康检测状态"""
        return {
            "enabled": Config.AUTO_HEALTH_CHECK,
            "running": ToolRegistry._auto_check_running,
            "interval": Config.HEALTH_CHECK_INTERVAL,
            "last_check": ToolRegistry._last_check_time.isoformat() if ToolRegistry._last_check_time else None,
            "stats": ToolRegistry._check_stats
        }
