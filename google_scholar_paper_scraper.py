#!/usr/bin/env python3
import os
import re
import glob
import time
import requests
import pandas as pd
import argparse
from urllib.parse import urlparse, parse_qs, urljoin
from lxml import html

# Optional selenium imports
try:
    import undetected_chromedriver as uc
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

driver = None

def get_author_id(author_name, link=None):
    """Extract author ID from Google Scholar link or search by name."""
    if link:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        author_id = params.get("user", [""])[0]
        return author_id
        
    URL = "https://scholar.google.com/scholar"
    PARAMS = {"as_sdt": "0,5", "q": author_name, "btnG": "", "hl":"en"}
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Referer': 'https://scholar.google.com/',
        'Connection': 'keep-alive'
    }
    
    resp = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=20)
    tree = html.fromstring(resp.text)
    table = tree.xpath('//div[@role="main"]//div[@class="gs_r"]//table')[0]
    link = table.xpath('.//a/@href')[0]
    
    parsed = urlparse(link)
    params = parse_qs(parsed.query)
    author_id = params.get("user", [""])[0]
    return author_id

def get_cookies():
    """Try to spoof some realistic cookies (experimental)"""
    response = requests.get(
        'https://scholar.google.com',
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=10
    )
    return response.cookies.get_dict() if response.status_code == 200 else {}


def get_driver():
    """Initialize selenium driver if available."""
    global driver
    if not SELENIUM_AVAILABLE:
        return None
        
    if driver is not None:
        try:
            driver.title
            return driver
        except:
            driver.quit()
            driver = None

    options = uc.ChromeOptions()
    prefs = {
        "download.default_directory": os.getcwd(),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.automatic_downloads": 1
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    driver = uc.Chrome(options=options)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": os.getcwd()
    })
    return driver

def download_with_selenium(link):
    """Download using selenium."""
    if not SELENIUM_AVAILABLE:
        print("Selenium not available, skipping")
        return
    
    d = get_driver()
    if d:
        d.get(link)
        time.sleep(10)

def _fetch_and_parse(author_id, link, pagination):
    """Fetch paper URLs from author page - simplified to just get links."""
    BASE = "https://scholar.google.com"
    URL = f"{BASE}/citations"
    
    PARAMS = {
        "user": get_author_id(author_id, link),
        "oi": "ao",
        "cstart": pagination,
        "pagesize": "100",
        "hl":"en"
    }
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Referer': 'https://scholar.google.com/',
        'Connection': 'keep-alive'
    }
    
    r = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=20, cookies=get_cookies())
    r.raise_for_status()
    
    doc = html.fromstring(r.text)
    rows = doc.xpath('//tr[@class="gsc_a_tr"]')
    
    results = []
    for tr in rows:
        title_el = tr.xpath('.//a[@class="gsc_a_at"]')
        title = title_el[0].text_content().strip() if title_el else ""
        
        href_rel = title_el[0].get("href") if title_el else ""
        href = urljoin(BASE, href_rel) if href_rel else ""
        
        # Just get minimal info needed for initial processing
        results.append({
            "title": title,  # For display purposes only
            "url": href
        })
    return results

def fetch_and_parse(author_id, link=None):
    """Fetch all publications for an author."""
    results = []
    pagination = 0
    while True:
        items = _fetch_and_parse(author_id, link, pagination)
        if len(results) > 0 and results[-1] == items[-1]:
            break
        results.extend(items)
        pagination += 100
    return results

def _safe_filename(name: str, default: str = "paper") -> str:
    """Make filename filesystem-safe."""
    name = re.sub(r"[^A-Za-z0-9\-\._ ]+", "_", name).strip()
    return name or default

def extract_paper_metadata(doc):
    """Extract metadata from the paper page - simplified approach."""
    metadata = {}
    
    # Find all field rows in the metadata table
    fields = doc.xpath('//div[@id="gsc_oci_table"]//div[@class="gs_scl"]')
    
    for field_div in fields:
        # Get field name and value
        field_name_els = field_div.xpath('.//div[@class="gsc_oci_field"]')
        field_value_els = field_div.xpath('.//div[@class="gsc_oci_value"]')
        
        if field_name_els and field_value_els:
            field_name = field_name_els[0].text_content().strip()
            if field_name == 'Total citations':
                field_value = field_value_els[0].getchildren()[0].text_content()
                # citation_match = re.search(r'Cited by (\d+)', field_value)
                # field_value = int(citation_match.group(1)) if citation_match else 0
            else:
                field_value = field_value_els[0].text_content().strip()
            
            # Store the exact field names as they appear
            if field_name == "Authors":
                metadata["authors"] = field_value
            elif field_name == "Publication date":
                metadata["publication_date"] = field_value
            elif field_name == "Journal":
                metadata["journal"] = field_value
            elif field_name == "Volume":
                metadata["volume"] = field_value
            elif field_name == "Issue":
                metadata["issue"] = field_value
            elif field_name == "Pages":
                metadata["pages"] = field_value
            elif field_name == "Publisher":
                metadata["publisher"] = field_value
            elif field_name == "Description":
                metadata["description"] = field_value.replace('\xa0', ' ')
            elif field_name == "Total citations":
                # Extract number from "Cited by 2515"
                citation_match = re.search(r'Cited by (\d+)', field_value)
                metadata["total_citations_detailed"] = int(citation_match.group(1)) if citation_match else 0
    
    return metadata

def download_pdf_from_scholar_article(article_url: str, paper_title: str, year: int = None, use_selenium: bool = False) -> tuple:
    """Download PDF and extract metadata from Scholar paper page - simplified."""
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Referer': 'https://scholar.google.com/',
        'Connection': 'keep-alive'
    }
    BASE = "https://scholar.google.com"
    
    # Make one request to get the paper page
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=25, allow_redirects=True, cookies=get_cookies())
        if r.status_code != 200:
            print(f"Skipping (HTTP {r.status_code}): {article_url}")
            return None, None
            
        doc = html.fromstring(r.text)
        
    except requests.RequestException as e:
        print(f"Skipping (request failed): {article_url} ({e})")
        return None, None

    # Extract metadata using the simplified function
    metadata = extract_paper_metadata(doc)

    # print(metadata)

    # Look for PDF links
    pdf_anchors = doc.xpath('//div[@role="main"]//div[@id="gsc_oci_title_wrapper"]//div[@class="gsc_oci_title_ggi"]//a')
    
    if not pdf_anchors:
        print(f"No PDF link found, skipping: {article_url}")
        return None, metadata

    # Find the best PDF link
    pdf_href = None
    if len(pdf_anchors) > 1:
        # Prefer links with "pdf" in the text
        for anchor in pdf_anchors:
            if 'pdf' in anchor.text_content().lower():
                pdf_href = anchor.get("href")
                break
        if not pdf_href:
            pdf_href = pdf_anchors[-1].get("href")
    else:
        pdf_href = pdf_anchors[0].get("href")

    if not pdf_href:
        print(f"No valid PDF link found, skipping: {article_url}")
        return None, metadata

    pdf_url = urljoin(article_url if article_url.startswith("http") else BASE, pdf_href)

    # Try downloading the PDF
    try:
        pr = requests.get(pdf_url, headers=HEADERS, timeout=60, allow_redirects=True, stream=True, cookies=get_cookies())
        if pr.status_code != 200:
            if use_selenium:
                print(f"Failed to fetch PDF, trying selenium: {pdf_url}")
                try:
                    download_with_selenium(pdf_url)
                    return "downloaded_with_selenium", metadata
                except Exception as e:
                    print(f"Selenium failed: {e}")
                    return None, metadata
            else:
                print(f"Failed to fetch PDF: {pdf_url}")
                return None, metadata
    except requests.RequestException as e:
        print(f"PDF download failed: {e}")
        return None, metadata

    # Save the PDF
    base_name = f"{paper_title}({year})" if year else paper_title
    filename = _safe_filename(base_name) + ".pdf"

    try:
        with open(filename, "wb") as f:
            for chunk in pr.iter_content(chunk_size=64*1024):
                if chunk:
                    f.write(chunk)
    except OSError as e:
        print(f"Failed to save PDF: {e}")
        return None, metadata

    print(f"Downloaded: {filename}")
    return os.path.abspath(filename), metadata

def load_data_source(source: str):
    """
    Load data from CSV file or Google Sheets URL.
    
    Args:
        source: Path to CSV file or Google Sheets share URL
        
    Returns:
        pandas.DataFrame: Loaded data
    """
    if source.startswith('http') and 'docs.google.com/spreadsheets' in source:
        # Convert Google Sheets URL to CSV export URL
        if '/d/' in source:
            sheet_id = source.split('/d/')[1].split('/')[0]
        else:
            raise ValueError("Invalid Google Sheets URL. Use the shareable link.")
        
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        print(f"Loading data from Google Sheets: {sheet_id}")
        df = pd.read_csv(csv_url)
    else:
        # Regular CSV file
        print(f"Loading data from CSV file: {source}")
        df = pd.read_csv(source)
    
    return df

def scrape_author_papers(scholar_link: str, use_selenium: bool = False, max_papers: int = -1):
    """
    Scrape papers for a single author from Google Scholar.
    
    Args:
        scholar_link: Google Scholar profile URL
        use_selenium: Whether to use selenium for failed downloads
        max_papers: Maximum number of papers to download (-1 for all)
    """
    # Extract author name from URL for folder naming
    author_id = get_author_id(None, scholar_link)
    
    # Get author publications
    items = fetch_and_parse(author_id, scholar_link)
    
    # Create folder without timestamp
    folder_name = f"output/author_{author_id}"
    os.makedirs(folder_name, exist_ok=True)
    
    # Change to author folder
    original_dir = os.getcwd()
    os.chdir(folder_name)
    
    try:
        # Get already downloaded papers (check existing files)
        existing_pdfs = glob.glob('*.pdf')
        downloaded_papers = set()
        
        # Create a set of existing paper identifiers for quick lookup
        for pdf_file in existing_pdfs:
            downloaded_papers.add(pdf_file)
        
        print(f"Found {len(downloaded_papers)} existing papers in folder")
        
        # Prepare metadata collection
        papers_metadata = []
        
        # Download papers
        downloaded_count = 0
        skipped_count = 0
        
        for i, item in enumerate(items):
            if max_papers > 0 and downloaded_count >= max_papers:
                break
            
            print(f"Processing ({i+1}/{len(items)}): {item['title']}")
            
            # Get metadata from individual paper page
            result, metadata = download_pdf_from_scholar_article(
                item['url'], 
                item['title'],  # This is just for display, real title comes from metadata
                None,  # Year will come from metadata
                use_selenium
            )
            
            if not metadata:
                print(f"Failed to extract metadata for: {item['title']}")
                continue
            
            # Use metadata to create filename and check duplicates  
            title = item['title']  # Use the paper title from author page
            year = None
            if metadata.get('publication_date'):
                # Try to extract year from publication date
                year_match = re.search(r'(\d{4})', metadata['publication_date'])
                year = int(year_match.group(1)) if year_match else None
            
            base_name = f"{title}({year})" if year else title
            filename = _safe_filename(base_name) + ".pdf"
            
            # Prepare paper record with metadata as primary source
            paper_record = {
                'title': title,
                'authors': metadata.get('authors', ''),
                'publication_date': metadata.get('publication_date', ''),
                'journal': metadata.get('journal', ''),
                'volume': metadata.get('volume', ''),
                'issue': metadata.get('issue', ''),
                'pages': metadata.get('pages', ''),
                'publisher': metadata.get('publisher', ''),
                'description': metadata.get('description', ''),
                'total_citations': metadata.get('total_citations_detailed', 0),
                'scholar_url': item['url'],
                'pdf_filename': filename
            }
            
            if filename in downloaded_papers:
                print(f"Already exists: {filename}")
                skipped_count += 1
                paper_record['download_status'] = 'already_exists'
            elif result:
                downloaded_count += 1
                downloaded_papers.add(filename)
                paper_record['download_status'] = 'downloaded'
            else:
                paper_record['download_status'] = 'failed'
            
            papers_metadata.append(paper_record)
        
        # Save metadata to CSV
        if papers_metadata:
            metadata_df = pd.DataFrame(papers_metadata)
            csv_filename = f"papers_metadata.csv"
            metadata_df.to_csv(csv_filename, index=False, encoding='utf-8')
            print(f"Saved metadata to: {csv_filename}")
                
        print(f"\nSummary for {folder_name}:")
        print(f"- Downloaded: {downloaded_count} new papers")
        print(f"- Skipped: {skipped_count} existing papers")
        print(f"- Total papers in folder: {len(glob.glob('*.pdf'))}")
        
    finally:
        os.chdir(original_dir)
        # Close selenium driver if used
        global driver
        if driver:
            driver.quit()
            driver = None

def scrape_from_source(source: str, use_selenium: bool = False, max_papers: int = -1, column_name: str = "Google Scholar Page"):
    """
    Scrape papers for multiple authors from CSV file or Google Sheets.
    
    Args:
        source: Path to CSV file or Google Sheets share URL
        use_selenium: Whether to use selenium for failed downloads
        max_papers: Maximum number of papers per author (-1 for all)
        column_name: Name of the column containing Scholar links
    """
    df = load_data_source(source)
    
    # Check for the specified column name, fallback to 'scholar_link'
    if column_name not in df.columns:
        if 'scholar_link' in df.columns:
            column_name = 'scholar_link'
            print(f"Column '{column_name}' not found, using 'scholar_link' instead")
        else:
            available_cols = ', '.join(df.columns.tolist())
            raise ValueError(f"Column '{column_name}' not found. Available columns: {available_cols}")
    
    print(f"Found {len(df)} authors to process")
    
    for idx, row in df.iterrows():
        scholar_link = row[column_name]
        
        # Skip empty rows
        if pd.isna(scholar_link) or not scholar_link.strip():
            print(f"Skipping row {idx+1}: empty scholar link")
            continue
            
        print(f"\nProcessing author {idx+1}/{len(df)}: {scholar_link}")
        
        try:
            scrape_author_papers(scholar_link, use_selenium, max_papers)
        except Exception as e:
            print(f"Error processing {scholar_link}: {e}")
            continue

def scrape_from_csv(csv_file: str, use_selenium: bool = False, max_papers: int = -1):
    """Legacy function - use scrape_from_source instead."""
    return scrape_from_source(csv_file, use_selenium, max_papers, "scholar_link")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Download papers from Google Scholar')
    
    # Input source (mutually exclusive group)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--scholar-link', '-s', 
                            help='Single Google Scholar profile URL')
    source_group.add_argument('--sheets-url', '-g', 
                            help='Google Sheets URL with scholar links')
    source_group.add_argument('--csv-file', '-c', 
                            help='CSV file path with scholar links')
    
    # Options
    parser.add_argument('--selenium', action='store_true', 
                       help='Use selenium for failed downloads')
    parser.add_argument('--max-papers', '-m', type=int, default=-1,
                       help='Maximum papers per author (-1 for all)')
    parser.add_argument('--column', default='Google Scholar Page',
                       help='Column name for scholar links (default: "Google Scholar Page")')
    
    args = parser.parse_args()
    
    print("Google Scholar Paper Scraper")
    print("=" * 40)
    
    try:
        if args.scholar_link:
            print(f"Processing single author: {args.scholar_link}")
            scrape_author_papers(args.scholar_link, args.selenium, args.max_papers)
            
        elif args.sheets_url:
            print(f"Processing Google Sheets: {args.sheets_url}")
            scrape_from_source(args.sheets_url, args.selenium, args.max_papers, args.column)
            
        elif args.csv_file:
            print(f"Processing CSV file: {args.csv_file}")
            scrape_from_source(args.csv_file, args.selenium, args.max_papers, args.column)
            
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Clean up selenium driver
        if driver:
            driver.quit()
        print("Done!")