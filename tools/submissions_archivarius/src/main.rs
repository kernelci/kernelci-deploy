use anyhow::Result;
use chrono::{DateTime, Local};
use clap::Parser;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};
use tar::Builder;
use xz2::write::XzEncoder;

#[derive(Parser)]
#[command(name = "submissions_archivarius")]
#[command(about = "Archive files from source directory to destination with xz compression")]
struct Args {
    #[arg(long = "sourcedir", env = "ARCHIVARIUS_SRCDIR")]
    source_dir: PathBuf,

    #[arg(long = "destdir", env = "ARCHIVARIUS_DESTDIR")]
    dest_dir: PathBuf,

    #[arg(long = "number", env = "ARCHIVARIUS_NUMBER", default_value = "50000")]
    number: usize,

    #[arg(short = 'v', long = "verbose")]
    verbose: bool,

    #[arg(long = "upload", env = "ARCHIVARIUS_UPLOAD")]
    upload: Option<String>,

    #[arg(long = "compression-level", env = "ARCHIVARIUS_COMPRESSION_LEVEL", default_value = "1")]
    compression_level: u32,

    #[arg(long = "suffix", env = "ARCHIVARIUS_SUFFIX", default_value = ".json")]
    suffix: String,
}

#[derive(Debug)]
struct FileInfo {
    path: PathBuf,
    created: SystemTime,
}

fn get_sorted_files(dir: &Path, suffix: &str, verbose: bool) -> Result<Vec<FileInfo>> {
    let mut files = Vec::new();
    
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        
        if path.is_file() {
            // Check if file has the required suffix (in kcidb-rest case, .json)
            if let Some(file_name) = path.file_name().and_then(|n| n.to_str()) {
                if file_name.ends_with(suffix) {
                    let metadata = entry.metadata()?;
                    let created = metadata.created().unwrap_or(metadata.modified()?);
                    files.push(FileInfo { path, created });
                }
            }
        }
    }
    
    // Sort by creation time (oldest first)
    files.sort_by_key(|f| f.created);
    
    if verbose {
        println!("  Total files found: {}", files.len());
        if !files.is_empty() {
            let oldest: DateTime<Local> = files[0].created.into();
            let newest: DateTime<Local> = files[files.len()-1].created.into();
            println!("  Oldest file: {} ({})", oldest.format("%Y-%m-%d %H:%M:%S"), files[0].path.file_name().unwrap_or_default().to_string_lossy());
            println!("  Newest file: {} ({})", newest.format("%Y-%m-%d %H:%M:%S"), files[files.len()-1].path.file_name().unwrap_or_default().to_string_lossy());
        }
    }
    
    Ok(files)
}

fn create_archive_name(first_file_time: SystemTime) -> String {
    let datetime: DateTime<Local> = first_file_time.into();
    format!("submissions-{}.tar.xz", datetime.format("%Y%m%d-%H%M%S"))
}

fn create_xz_archive(files: &[FileInfo], archive_path: &Path, compression_level: u32, verbose: bool) -> Result<()> {
    let archive_file = fs::File::create(archive_path)?;
    let xz_encoder = XzEncoder::new(archive_file, compression_level);
    let mut tar_builder = Builder::new(xz_encoder);

    for (i, file_info) in files.iter().enumerate() {
        let file_name = file_info.path.file_name()
            .ok_or_else(|| anyhow::anyhow!("Invalid file name"))?;
        
        if verbose && i % 1000 == 0 {
            println!("  Adding to archive: {} files processed...", i + 1);
        }
        
        tar_builder.append_path_with_name(&file_info.path, file_name)?;
    }

    tar_builder.finish()?;
    Ok(())
}

async fn upload_archive(archive_path: &Path, upload_config: &str, verbose: bool) -> Result<()> {
    // Stub implementation for upload functionality
    if verbose {
        println!("  Starting upload process...");
        println!("  Archive size: {} bytes", fs::metadata(archive_path)?.len());
    }
    println!("Upload stub: Would upload {} to {}", archive_path.display(), upload_config);
    
    // TODO: Implement actual upload logic here for kernelci-storage
    
    Ok(())
}

async fn process_files(args: &Args) -> Result<()> {
    // Print configuration in verbose mode
    if args.verbose {
        println!("\n=== Configuration ===");
        println!("  Compression level: {}", args.compression_level);
        println!("  File suffix filter: {}", args.suffix);
        println!("\n=== Initial scan of source directory ===");
    }
    let mut files = get_sorted_files(&args.source_dir, &args.suffix, args.verbose)?;
    
    loop {
        if files.len() >= args.number {
            let files_to_archive = &files[..args.number];
            let first_file_time = files_to_archive[0].created;
            let archive_name = create_archive_name(first_file_time);
            let archive_path = args.dest_dir.join(&archive_name);
            
            println!("Creating archive: {}", archive_path.display());
            println!("Archiving {} files", files_to_archive.len());
            
            if args.verbose {
                println!("\n=== Creating archive ===");
                let first_datetime: DateTime<Local> = first_file_time.into();
                println!("  Archive name based on first file time: {}", first_datetime.format("%Y-%m-%d %H:%M:%S"));
            }
            
            create_xz_archive(files_to_archive, &archive_path, args.compression_level, args.verbose)?;
            
            if args.verbose {
                let archive_size = fs::metadata(&archive_path)?.len();
                println!("  Archive created successfully: {} bytes", archive_size);
            }

            // TODO: delete archived files first or upload first?
            
            // Upload if configured
            if let Some(upload_config) = &args.upload {
                if args.verbose {
                    println!("\n=== Uploading archive ===");
                }
                upload_archive(&archive_path, upload_config, args.verbose).await?;
                
                // Delete archive after successful upload
                if args.verbose {
                    println!("  Deleting local archive after successful upload");
                }
                fs::remove_file(&archive_path)?;
                println!("Archive uploaded and deleted locally");
            }
            
            // Remove archived files from source directory
            if args.verbose {
                println!("\n=== Cleaning up source files ===");
            }
            for (i, file_info) in files_to_archive.iter().enumerate() {
                fs::remove_file(&file_info.path)?;
                if args.verbose && i % 1000 == 0 {
                    println!("  Removing files: {} processed...", i + 1);
                }
            }
            
            // Remove processed files from the list (first N files)
            files.drain(..args.number);
            
            println!("Archive created successfully: {}", archive_name);
            if args.verbose {
                println!("=== Archiving cycle completed ===");
                println!("  Remaining files to process: {}", files.len());
                println!();
            }
        } else {
            println!(
                "Not enough files ({} < {}), sleeping for 60 seconds...", 
                files.len(), 
                args.number
            );
            if args.verbose {
                println!("  Files needed: {} more", args.number - files.len());
                let now: DateTime<Local> = Local::now();
                println!("  Next check at: {}", (now + chrono::Duration::seconds(60)).format("%H:%M:%S"));
            }
            tokio::time::sleep(Duration::from_secs(60)).await;
            
            // Rescan for new files and merge with existing list
            if args.verbose {
                println!("\n=== Checking for new files ===");
            }
            let new_files = get_sorted_files(&args.source_dir, &args.suffix, false)?; // Don't print details for new scan
            if args.verbose {
                println!("  New scan found {} files", new_files.len());
            }
            // clear our current list
            files.clear();

            files = new_files;
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    
    // Validate directories exist
    if !args.source_dir.exists() {
        return Err(anyhow::anyhow!("Source directory does not exist: {}", args.source_dir.display()));
    }
    
    if !args.dest_dir.exists() {
        fs::create_dir_all(&args.dest_dir)?;
        println!("Created destination directory: {}", args.dest_dir.display());
    }
    
    println!("Starting submissions archivarius...");
    println!("Source directory: {}", args.source_dir.display());
    println!("Destination directory: {}", args.dest_dir.display());
    println!("Files threshold: {}", args.number);
    if args.verbose {
        println!("Verbose mode: enabled");
    }
    
    if let Some(upload_config) = &args.upload {
        println!("Upload configured: {}", upload_config);
    }
    
    process_files(&args).await
}